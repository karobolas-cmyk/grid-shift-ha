"""Opt-in, bounded thermostat setpoints using the native published price series."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from math import ceil, floor, isfinite

from homeassistant.exceptions import ServiceValidationError

from .measurements import report_time
from .price import current_slot
from .safety import sensor_age_seconds


def number(value):
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (ValueError, TypeError):
        return None


def targets_match(left: dict, right: dict, tolerance: float = 0.05) -> bool:
    return (
        bool(left)
        and left.keys() == right.keys()
        and all(
            number(right[key]) is not None and abs(float(value) - float(right[key])) <= tolerance
            for key, value in left.items()
        )
    )


def target_state(state) -> dict:
    """Read the target, never infer it from measured room temperature."""
    if state is None:
        return {}
    attributes = state.attributes
    keys = (
        ("target_temp_low", "target_temp_high") if state.state == "heat_cool" else ("temperature",)
    )
    return {key: float(attributes[key]) for key in keys if number(attributes.get(key)) is not None}


def effective_direction(rule: dict, state) -> str | None:
    """Never change HVAC mode or turn an inactive device on."""
    if state is None or state.state not in {"heat", "cool", "heat_cool"}:
        return None
    requested = rule.get("direction", "auto")
    return state.state if requested in {"auto", state.state} else None


def cop(profile: str, scop: float, outdoor: float, direction: str) -> float:
    if profile == "direct_electric":
        return 1.0
    if profile == "ground_source":
        return max(1.2, scop)
    # An approximate temperature correction, not a manufacturer performance map.
    correction = (25 - outdoor) * 0.035 if direction == "cool" else (outdoor - 7) * 0.035
    return max(1.2, min(6.5, scop + correction))


def weather_temperature(rows, instant: datetime, fallback: float) -> float:
    for row in rows:
        try:
            stamp = datetime.fromisoformat(row["datetime"])
            value = number(row.get("temperature"))
            if value is not None and 0 <= (instant - stamp).total_seconds() < 3600:
                return value
        except (ValueError, KeyError, TypeError):
            continue
    return fallback


def limit_temperature_adjustment(rule: dict, state, target: dict) -> dict:
    """Apply the selected policy to plans, restoration and the final physical write."""
    policy = rule.get("adjustment", "both")
    if policy == "both":
        return target
    direction = effective_direction(rule, state)
    if policy == "lower_only" and direction != "heat":
        raise ValueError("lower_only_requires_heating")
    if direction not in {"heat", "cool"}:
        return target
    value = number(target.get("temperature"))
    if value is None:
        raise ValueError("target_unavailable")
    baseline = float(rule["baseline"])
    step = number(state.attributes.get("target_temp_step")) or 0.5
    if direction == "cool":
        # A cooling setback saves energy by raising the target; never pre-cool.
        bound = ceil(baseline / step - 1e-9) * step
        return {"temperature": round(max(value, bound), 2)}
    upper = baseline
    if policy == "lower_only":
        observed = number(target_state(state).get("temperature"))
        if observed is None:
            raise ValueError("target_unavailable")
        if observed < baseline - float(rule["max_deviation"]):
            return {"temperature": observed}  # Preserve a lower manual setting.
        upper = min(upper, observed)
    upper = floor(upper / step + 1e-9) * step
    if upper < max(
        baseline - float(rule["max_deviation"]), number(state.attributes.get("min_temp")) or 7,
    ):
        raise ValueError("temperature_band_has_no_supported_step")
    return {"temperature": round(min(value, upper), 2)}


def thermal_target(
    rule: dict, state, slots, now: datetime, outdoor: float | None, weather=()
) -> dict:
    """Return a bounded target for this zone; only published prices participate."""
    baseline = float(rule["baseline"])
    deviation = float(rule["max_deviation"])
    direction = effective_direction(rule, state)
    if direction is None:
        raise ValueError("inactive_or_incompatible_hvac_mode")
    low = max(baseline - deviation, number(state.attributes.get("min_temp")) or 7)
    high = min(baseline + deviation, number(state.attributes.get("max_temp")) or 35)
    if not low <= baseline <= high:
        raise ValueError("baseline_outside_device_limits")
    active = current_slot(slots, now)
    offset = 0.0
    if active is not None and len(slots) >= 2 and outdoor is not None:
        efficiency = cop(rule["heater_type"], rule["scop"], outdoor, direction)
        costs = [
            slot.price
            / cop(
                rule["heater_type"],
                rule["scop"],
                weather_temperature(weather, slot.start, outdoor),
                direction,
            )
            for slot in slots
            if slot.end > now
        ]
        current = active.price / efficiency
        percentile = sum(cost < current for cost in costs) / max(1, len(costs) - 1)
        strength = rule["weight"] / 10
        if rule["mode"] == "peak":
            offset = -deviation * strength if percentile >= 0.85 else 0.0
        else:
            offset = (0.5 - percentile) * 2 * deviation * strength
        if direction == "cool":
            offset = -offset
            # Hydronic pre-cooling needs a dew-point guard; only price setback is allowed.
            if rule["heater_type"] not in {"air_air", "direct_electric"}:
                offset = max(0.0, offset)
    if direction == "heat_cool":
        # Preserve a neutral band instead of shifting both heating and cooling together.
        return limit_temperature_adjustment(rule, state, {
            "target_temp_low": round(max(low, baseline - deviation), 2),
            "target_temp_high": round(min(high, baseline + deviation), 2),
        })
    step = number(state.attributes.get("target_temp_step")) or 0.5
    value = min(high, max(low, baseline + offset))
    value = round(value / step) * step
    # Quantization may never move the target outside the user's or device's bounds.
    return limit_temperature_adjustment(
        rule, state, {"temperature": round(min(high, max(low, value)), 2)},
    )


class ThermalController:
    """Write only explicitly selected zones, with rate limits and manual ownership."""

    def __init__(self, hass, config: dict, storage) -> None:
        self.hass, self.config, self.storage = hass, config, storage
        self.snapshot = {
            "enabled": self.enabled, "zones": [],
            "configured_zone_count": len(self.config.get("climate_rules", [])),
        }
        self._lock = asyncio.Lock()
        self._weather_at = None
        self._weather = []
        self.phase_guard = None

    @property
    def enabled(self) -> bool:
        return self.storage.state.thermal_enabled

    def phase_reservations(self, now: datetime) -> tuple[float, float, float]:
        """Reserve incremental demand while phase meters catch up after a target increase."""
        result = [0.0, 0.0, 0.0]
        for zone in self.storage.state.thermal_state.values():
            try:
                expires = datetime.fromisoformat(zone.get("reserved_until", ""))
                if expires > now:
                    result = [a + float(b) for a, b in zip(result, zone["reserved_a"], strict=True)]
            except (ValueError, KeyError, TypeError):
                continue
        return tuple(result)

    async def async_set_enabled(self, enabled: bool, now: datetime) -> None:
        if enabled and not self.config.get("climate_rules"):
            raise ServiceValidationError(
                "No thermostat zones are saved. Select zones in GridShift and save the "
                "configuration to Home Assistant before enabling climate control."
            )
        self.storage.state.thermal_enabled = enabled
        await self.storage.async_save()
        if not enabled:
            await self.async_release(now)

    async def _outdoor(self, now: datetime) -> float | None:
        entity_id = self.config.get("weather_entity")
        if entity_id and self.hass.services.has_service("weather", "get_forecasts"):
            due = self._weather_at is None or (now - self._weather_at).total_seconds() >= 1800
            if due:
                self._weather_at = now
                try:
                    async with asyncio.timeout(15):
                        data = await self.hass.services.async_call(
                            "weather",
                            "get_forecasts",
                            {"entity_id": entity_id, "type": "hourly"},
                            blocking=True,
                            return_response=True,
                        )
                    self._weather = (data or {}).get(entity_id, {}).get("forecast", [])
                except Exception:
                    pass
            for row in self._weather:
                try:
                    stamp = datetime.fromisoformat(row["datetime"])
                    if abs((stamp - now).total_seconds()) <= 3600:
                        return number(row.get("temperature"))
                except (ValueError, KeyError, TypeError):
                    continue
        state = self.hass.states.get(self.config.get("outdoor_temperature_entity", ""))
        if state and sensor_age_seconds(now, report_time(state)) <= 1800:
            return number(state.state)
        weather = self.hass.states.get(entity_id) if entity_id else None
        if weather and sensor_age_seconds(now, report_time(weather)) <= 1800:
            return number(weather.attributes.get("temperature"))
        return None

    def _phase_demand(self, rule: dict) -> tuple[float, float, float]:
        amps = rule["max_power_w"] / self.config["nominal_voltage_v"]
        phase = rule["phase"]
        if phase == "3_phase":
            return (amps / 3,) * 3
        if phase == "unknown":
            return (amps,) * 3  # Any one phase could carry the full load.
        return tuple(amps if phase == name else 0.0 for name in ("L1", "L2", "L3"))

    async def _write(self, entity_id: str, target: dict, now: datetime, zone: dict) -> None:
        rule = next(
            (rule for rule in self.config.get("climate_rules", [])
             if rule["entity_id"] == entity_id), zone.get("policy_rule", {}),
        )
        zone["policy_rule"] = dict(rule)
        zone.update(status="pending", requested=target, attempted_at=now.isoformat())
        await self.storage.async_save()
        # Re-read after persistence: a manual change during the await must not
        # turn a lower-only command or restoration into a temperature increase.
        try:
            observed = target_state(self.hass.states.get(entity_id))
            requested = dict(target)
            target = limit_temperature_adjustment(rule, self.hass.states.get(entity_id), target)
        except ValueError as err:
            zone.update(status=str(err))
            await self.storage.async_save()
            return
        if targets_match(target, observed):
            zone.update(
                status="manual_restore_required"
                if rule.get("adjustment") == "lower_only"
                and not targets_match(requested, observed) else "confirmed",
                requested=target, observed=observed,
            )
            await self.storage.async_save()
            return
        zone["requested"] = target
        try:
            async with asyncio.timeout(15):
                await self.hass.services.async_call(
                    "climate",
                    "set_temperature",
                    {"entity_id": entity_id, **target},
                    blocking=True,
                )
            observed = target_state(self.hass.states.get(entity_id))
            zone.update(
                last_write=now.isoformat(),
                last_command=target,
                observed=observed,
                status="confirmed" if targets_match(target, observed) else "awaiting_readback",
            )
            zone.pop("last_attempt", None)
            zone.pop("error", None)
        except Exception as err:
            zone.update(status="failed", error=type(err).__name__, last_attempt=now.isoformat())
        await self.storage.async_save()

    async def async_release(self, now: datetime) -> None:
        """Restore only targets still owned by us; preserve external/manual changes."""
        async with self._lock:
            for entity_id, zone in self.storage.state.thermal_state.items():
                state = self.hass.states.get(entity_id)
                if (
                    state
                    and state.state not in {"off", "unknown", "unavailable"}
                    and targets_match(
                        target_state(state),
                        zone.get("last_command", {}),
                    )
                    and zone.get("original")
                ):
                    await self._write(entity_id, zone["original"], now, zone)
                    if zone["status"] == "confirmed":
                        zone.clear()
                    elif zone["status"] in {
                        "manual_restore_required", "lower_only_requires_heating",
                    }:
                        zone.clear()
                        zone["status"] = "manual_restore_required"
                elif zone.get("original"):
                    zone.clear()
            await self.storage.async_save()

    async def async_evaluate(self, slots, now: datetime, currents, stale_phases) -> None:
        """Plan every zone; at most one non-emergency write per five-minute window."""
        async with self._lock:
            outdoor = await self._outdoor(now) if self.enabled else None
            rows = []
            stored = self.storage.state.thermal_state
            latest = [
                datetime.fromisoformat(item.get("last_attempt") or item["last_write"])
                for item in stored.values()
                if item.get("last_attempt") or item.get("last_write")
            ]
            can_write = (
                not latest
                or (now - max(latest)).total_seconds()
                >= self.config["thermal_min_interval_seconds"]
            )
            for rule in self.config.get("climate_rules", []):
                entity_id = rule["entity_id"]
                state = self.hass.states.get(entity_id)
                zone = stored.setdefault(entity_id, {})
                row = {
                    "entity_id": entity_id,
                    "baseline": rule["baseline"],
                    "max_deviation": rule["max_deviation"],
                    "adjustment": rule.get("adjustment", "both"),
                    "status": "off",
                    "target": {},
                }
                rows.append(row)
                if not self.enabled:
                    if zone.get("original"):
                        row.update(status="restore_unconfirmed", error=zone.get("error"))
                    elif zone.get("status") == "manual_restore_required":
                        row["status"] = "manual_restore_required"
                    continue
                if effective_direction(rule, state) is None:
                    row["status"] = "inactive_or_incompatible_hvac_mode"
                    continue
                if (
                    sensor_age_seconds(now, report_time(state))
                    > self.config["thermal_sensor_max_age_seconds"]
                    or number(state.attributes.get("current_temperature")) is None
                ):
                    row["status"] = "stale_temperature"
                    continue
                observed = target_state(state)
                last_command = zone.get("last_command")
                if last_command and not targets_match(observed, last_command):
                    pending = (
                        zone.get("status") == "awaiting_readback"
                        and (now - datetime.fromisoformat(zone["last_write"])).total_seconds() < 90
                    )
                    if pending:
                        row.update(
                            status="awaiting_readback", target=last_command, observed=observed
                        )
                        continue
                    if zone.get("status") == "awaiting_readback":
                        row.update(
                            status="readback_unconfirmed", target=last_command, observed=observed
                        )
                        continue
                    zone.clear()
                    zone["manual_until"] = (
                        now
                        + timedelta(
                            minutes=self.config["thermal_manual_hold_minutes"],
                        )
                    ).isoformat()
                if zone.get("manual_until") and datetime.fromisoformat(zone["manual_until"]) > now:
                    row["status"] = "manual_override"
                    continue
                try:
                    target = thermal_target(rule, state, slots, now, outdoor, self._weather)
                except ValueError as err:
                    row["status"] = str(err)
                    continue
                waiting = not slots or outdoor is None
                row.update(
                    target=target,
                    observed=observed,
                    direction=state.state,
                    status="waiting_prices"
                    if not slots
                    else "waiting_weather"
                    if waiting
                    else "planned",
                    cop=round(
                        cop(
                            rule["heater_type"],
                            rule["scop"],
                            outdoor if outdoor is not None else 7,
                            state.state,
                        ),
                        2,
                    ),
                )
                if targets_match(target, observed):
                    row["status"] = "baseline" if waiting else "confirmed"
                    if last_command:
                        zone["status"] = "confirmed"
                    continue
                # No valid plan: restore our baseline, do not claim an unowned thermostat.
                if waiting and not zone.get("original"):
                    continue
                increasing = (
                    bool(observed)
                    and state.state != "heat_cool"
                    and (
                        (target.get("temperature", 0) - observed.get("temperature", 0))
                        * (-1 if state.state == "cool" else 1)
                        > 0
                    )
                )
                if not observed:
                    row["status"] = "target_unavailable"
                    continue
                demand = self._phase_demand(rule) if increasing else (0.0,) * 3
                reserved = self.phase_reservations(now)
                if increasing and (
                    (self.phase_guard is not None and not self.phase_guard(demand))
                    or stale_phases
                    or any(
                        max(0, amp) + reserve + extra
                        > self.config["main_fuse_a"] - self.config["phase_safety_margin_a"]
                        for amp, reserve, extra in zip(currents, reserved, demand, strict=True)
                    )
                ):
                    row["status"] = "phase_limit"
                    continue
                if not can_write:
                    row["status"] = "rate_limited"
                    continue
                zone.setdefault("original", observed)
                # A physical target may change while a weather request is in flight.
                latest_state = self.hass.states.get(entity_id)
                if effective_direction(rule, latest_state) != effective_direction(
                    rule, state
                ) or not (targets_match(observed, target_state(latest_state))):
                    row["status"] = "state_changed_before_write"
                    continue
                # Reserve before awaiting the service so the battery cannot spend
                # the same phase headroom while this physical write is in flight.
                if increasing:
                    zone.update(
                        reserved_a=demand, reserved_until=(now + timedelta(minutes=15)).isoformat()
                    )
                await self._write(entity_id, target, now, zone)
                row.update(
                    status=zone["status"],
                    observed=zone.get("observed", observed),
                    error=zone.get("error"),
                )
                can_write = False
            self.snapshot = {
                "enabled": self.enabled,
                "configured_zone_count": len(self.config.get("climate_rules", [])),
                "evaluated_at": now.isoformat(),
                "outdoor_temperature": outdoor,
                "zones": rows,
                "phase_reservations_a": self.phase_reservations(now),
            }
            await self.storage.async_save()
