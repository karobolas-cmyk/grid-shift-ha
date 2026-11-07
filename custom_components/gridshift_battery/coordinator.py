"""Data collection, learning, planning, and actuation coordinator."""

from __future__ import annotations

import logging
import math
from dataclasses import replace
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_CHARGE_SENSOR,
    CONF_BATTERY_DISCHARGE_SENSOR,
    CONF_BATTERY_POWER_DIRECTION,
    CONF_BATTERY_POWER_ENTITY,
    CONF_CAPACITY_TARIFF_ENABLED,
    CONF_CAPACITY_TARIFF_END_HOUR,
    CONF_CAPACITY_TARIFF_MODEL,
    CONF_CAPACITY_TARIFF_RATE_PER_KW,
    CONF_CAPACITY_TARIFF_REFERENCE_KW,
    CONF_CAPACITY_TARIFF_START_HOUR,
    CONF_CYCLE_COST,
    CONF_EV_POWER_ENTITIES,
    CONF_EV_POWER_ENTITY,
    CONF_EXPENSIVE_QUANTILE,
    CONF_GRID_EXPORT_SENSOR,
    CONF_HEATING_BALANCE_TEMP_C,
    CONF_HEATING_RESERVE_KW_PER_C,
    CONF_HORIZON_HOURS,
    CONF_HOUSE_LOAD_DIRECTION,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_HOUSE_LOAD_INCLUDES_EV,
    CONF_HOUSE_LOAD_IS_GRID_POWER,
    CONF_HOUSE_LOAD_SOURCE,
    CONF_INTERVAL_MINUTES,
    CONF_MAIN_FUSE_A,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MAX_DISCHARGE_POWER_W,
    CONF_MAX_TARGET_SOC_PCT,
    CONF_MIN_SAVINGS_PERCENT,
    CONF_MIN_SOC_PCT,
    CONF_NOMINAL_VOLTAGE_V,
    CONF_OUTDOOR_TEMPERATURE_ENTITY,
    CONF_PHASE_CURRENT_ENTITIES,
    CONF_PHASE_SAFETY_MARGIN_A,
    CONF_PHASE_SENSOR_MAX_AGE_SECONDS,
    CONF_PRICE_ENTITY,
    CONF_ROUND_TRIP_EFFICIENCY,
    CONF_SOC_ENTITY,
    CONF_SOLAR_PEAK_POWER_KWP,
    CONF_SOLAR_POWER_ENTITIES,
    CONF_SOLAR_POWER_ENTITY,
    CONF_SWEDISH_WINTER_MODE,
    CONF_WINTER_SOLAR_FACTOR,
    DEFAULTS,
    DOMAIN,
)
from .controller import BatteryController, LiveSafetyError, PowerReadbackError, RuntimeStorage
from .models import Decision, OptimizerInput
from .optimizer import optimize
from .power import (
    battery_power_inverted,
    grid_power_is_net,
    house_from_grid_w,
    house_input_is_grid,
    house_power_inverted,
    observed_grid_charge_w,
)
from .price import parse_price_slots
from .safety import sensor_age_seconds, stale_sensor_ids

_LOGGER = logging.getLogger(__name__)


def _is_swedish_winter(month: int, day: int) -> bool:
    return month > 11 or month < 3 or (month == 11 and day >= 15) or (
        month == 3 and day <= 15
    )


def merged_config(entry: ConfigEntry) -> dict[str, Any]:
    """Return entry data with user options and defaults applied."""
    return {**DEFAULTS, **entry.data, **entry.options}


class GridShiftCoordinator(DataUpdateCoordinator[Decision]):
    """Coordinate the entire local optimization loop."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        storage: RuntimeStorage,
        controller: BatteryController,
    ) -> None:
        self.entry = entry
        self.config = merged_config(entry)
        self.storage = storage
        self.controller = controller
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=self.config[CONF_INTERVAL_MINUTES]),
        )
        self.controller.power_guard = self._guard_power_write

    def _guard_power_write(self, requested_w: int, action: str) -> int:
        """Recheck live safety after settling and before each rate-write attempt."""
        currents, _, stale = self._phase_currents(dt_util.now())
        if stale:
            raise LiveSafetyError(f"phase_sensor_stale before rate write: {', '.join(stale)}")
        try:
            soc = self._value(self.config[CONF_SOC_ENTITY])
        except UpdateFailed as err:
            raise LiveSafetyError("state_of_charge_unavailable before rate write") from err
        if not 0 <= soc <= 100:
            raise LiveSafetyError("invalid_state_of_charge before rate write")
        if action == "charge":
            if soc >= self.config[CONF_MAX_TARGET_SOC_PCT]:
                raise LiveSafetyError("state_of_charge_limit before rate write")
            headroom = min(
                self.config[CONF_MAIN_FUSE_A]
                - self.config[CONF_PHASE_SAFETY_MARGIN_A]
                - max(0.0, current)
                for current in currents
            )
            safe_w = max(
                0.0,
                self._previous_grid_charge_w(dt_util.now())
                + headroom * self.config[CONF_NOMINAL_VOLTAGE_V] * 3,
            )
            command = min(requested_w, int(safe_w))
            if command < 100:
                raise LiveSafetyError("phase_capacity_exhausted before rate write")
            return command
        if not self.controller.discharge_enabled or soc <= self.config[CONF_MIN_SOC_PCT]:
            raise LiveSafetyError("discharge_safety_limit before rate write")
        return requested_w

    def _value(self, entity_id: str | None, default: float | None = None) -> float:
        if not entity_id:
            if default is None:
                raise UpdateFailed("required entity is not configured")
            return default
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            if default is None:
                raise UpdateFailed(f"{entity_id} is unavailable")
            return default
        try:
            return float(state.state)
        except ValueError as err:
            if default is not None:
                return default
            raise UpdateFailed(f"{entity_id} is not numeric") from err

    def _power_w(self, entity_id: str | None, default: float | None = None) -> float:
        value = self._value(entity_id, default)
        if not entity_id:
            return value
        state = self.hass.states.get(entity_id)
        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) if state else None
        factors = {"W": 1, "kW": 1000, "MW": 1_000_000}
        return value * factors.get(unit, 1)

    def _measured_power_w(self, entity_id: str, now) -> float:
        """Only derive consumption from fresh power measurements, never a setpoint."""
        state = self.hass.states.get(entity_id)
        stamp = getattr(state, "last_reported", None) or getattr(state, "last_updated", None)
        if state is None or sensor_age_seconds(now, stamp) > 120:
            raise UpdateFailed(f"{entity_id} has no fresh power measurement")
        if state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) not in {"W", "kW", "MW"}:
            raise UpdateFailed(f"{entity_id} must measure power")
        value = self._power_w(entity_id)
        if not math.isfinite(value):
            raise UpdateFailed(f"{entity_id} is not finite")
        return value

    def _sum_power_w(self, entity_ids: str | list[str] | None) -> float:
        """Return the sum of one or more power entities in watts."""
        if not entity_ids:
            return 0.0
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]
        return sum(self._power_w(entity_id, 0.0) for entity_id in entity_ids)

    def _current_a(self, entity_id: str) -> float:
        value = self._value(entity_id)
        state = self.hass.states.get(entity_id)
        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) if state else None
        return value / 1000 if unit == "mA" else value

    def _battery_power_w(self, now) -> float:
        """Return measured battery power, positive for charging."""
        net_entity = self.config.get(CONF_BATTERY_POWER_ENTITY)
        if net_entity:
            value = self._measured_power_w(net_entity, now)
            state = self.hass.states.get(net_entity)
            inverted = battery_power_inverted(
                net_entity, state.attributes.get("friendly_name", ""),
                self.config[CONF_BATTERY_POWER_DIRECTION],
            )
            return -value if inverted else value
        charge = self.config.get(CONF_BATTERY_CHARGE_SENSOR)
        discharge = self.config.get(CONF_BATTERY_DISCHARGE_SENSOR)
        if charge and discharge:
            return self._measured_power_w(charge, now) - self._measured_power_w(discharge, now)
        raise UpdateFailed("Measured battery power is required to derive house load")

    def _previous_grid_charge_w(self, now) -> int:
        """Do not add an undelivered command to the available phase headroom."""
        confirmed = self.controller.last_power_w
        if confirmed <= 0:
            return 0
        if not self.config.get(CONF_BATTERY_POWER_ENTITY) and not (
            self.config.get(CONF_BATTERY_CHARGE_SENSOR)
            and self.config.get(CONF_BATTERY_DISCHARGE_SENSOR)
        ):
            return confirmed  # Legacy mappings do not expose measured battery power.
        solar_ids = self.config.get(CONF_SOLAR_POWER_ENTITIES) or self.config.get(
            CONF_SOLAR_POWER_ENTITY
        ) or []
        if not solar_ids and self.config.get(CONF_SOLAR_PEAK_POWER_KWP, 0) > 0:
            return 0
        if isinstance(solar_ids, str):
            solar_ids = [solar_ids]
        try:
            solar_w = sum(self._measured_power_w(entity_id, now) for entity_id in solar_ids)
            return observed_grid_charge_w(confirmed, self._battery_power_w(now), solar_w)
        except UpdateFailed:
            return 0

    def _phase_currents(
        self, now
    ) -> tuple[tuple[float, float, float], float, tuple[str, ...]]:
        """Return phase currents, oldest age, and invalid or stale entity IDs."""
        entity_ids = tuple(self.config[CONF_PHASE_CURRENT_ENTITIES])
        if len(entity_ids) != 3:
            return (0.0, 0.0, 0.0), float("inf"), entity_ids

        states = tuple((entity_id, self.hass.states.get(entity_id)) for entity_id in entity_ids)
        stale = set(
            stale_sensor_ids(
                now,
                (
                    (entity_id, getattr(state, "last_updated", None))
                    for entity_id, state in states
                ),
                self.config[CONF_PHASE_SENSOR_MAX_AGE_SECONDS],
            )
        )
        ages = [
            sensor_age_seconds(now, getattr(state, "last_updated", None))
            for _, state in states
        ]
        currents: list[float] = []
        for entity_id, state in states:
            if state is None or state.state in ("unknown", "unavailable"):
                stale.add(entity_id)
                currents.append(0.0)
                continue
            try:
                currents.append(self._current_a(entity_id))
            except UpdateFailed:
                stale.add(entity_id)
                currents.append(0.0)
        return (
            (currents[0], currents[1], currents[2]),
            max(ages, default=float("inf")),
            tuple(entity_id for entity_id in entity_ids if entity_id in stale),
        )

    async def _phase_data_failsafe(
        self, oldest_age_seconds: float, stale_entities: tuple[str, ...]
    ) -> Decision:
        """Stop GridShift-owned control when phase protection has stale inputs."""
        reason = "phase_sensor_stale"
        if self.controller.enabled:
            try:
                await self.controller.async_release_control()
            except Exception:
                _LOGGER.exception("Could not return Fronius to Auto after stale phase data")
                reason = "phase_sensor_stale_release_failed"
        await self.storage.async_save()
        finite_age = oldest_age_seconds if oldest_age_seconds != float("inf") else None
        return Decision(
            "blocked",
            0,
            reason,
            discharge_power_w=0,
            phase_data_age_seconds=(
                round(finite_age, 1) if finite_age is not None else None
            ),
            stale_phase_entities=stale_entities,
        )

    async def _async_tibber_price_slots(self, now, horizon_end):
        if not self.hass.services.has_service("tibber", "get_prices"):
            return ()
        try:
            response = await self.hass.services.async_call(
                "tibber",
                "get_prices",
                {"start": now.isoformat(), "end": horizon_end.isoformat()},
                blocking=True,
                return_response=True,
            )
        except Exception:
            _LOGGER.debug("Could not fetch prices through tibber.get_prices", exc_info=True)
            return ()
        prices = []
        for home_prices in (response or {}).get("prices", {}).values():
            prices.extend(home_prices)
        return parse_price_slots({"prices": prices}, now)

    async def _async_update_data(self) -> Decision:
        now = dt_util.now()
        currents, phase_data_age, stale_phase_entities = self._phase_currents(now)
        if stale_phase_entities:
            return await self._phase_data_failsafe(
                phase_data_age, stale_phase_entities
            )
        price_state = self.hass.states.get(self.config[CONF_PRICE_ENTITY])
        if price_state is None:
            raise UpdateFailed("price entity is unavailable")
        slots = parse_price_slots(price_state.attributes, now, price_state.state)
        horizon_end = now + timedelta(hours=self.config[CONF_HORIZON_HOURS])
        if len(slots) < 2:
            slots = await self._async_tibber_price_slots(now, horizon_end)
        slots = tuple(slot for slot in slots if slot.end > now and slot.start < horizon_end)

        house_id = self.config[CONF_HOUSE_LOAD_ENTITY]
        house_state = self.hass.states.get(house_id)
        house_is_grid = house_input_is_grid(
            house_id, house_state.attributes.get("friendly_name", "") if house_state else "",
            self.config[CONF_HOUSE_LOAD_SOURCE],
            self.config.get(CONF_HOUSE_LOAD_IS_GRID_POWER, False),
        )
        house_inverted = house_power_inverted(house_id, self.config[CONF_HOUSE_LOAD_DIRECTION])
        house_sign = -1 if house_inverted else 1
        house_w = self._power_w(house_id) * house_sign
        solar_entities = self.config.get(CONF_SOLAR_POWER_ENTITIES) or self.config.get(
            CONF_SOLAR_POWER_ENTITY
        )
        solar_w = self._sum_power_w(solar_entities)
        if house_is_grid:
            house_w = self._measured_power_w(house_id, now) * house_sign
            solar_ids = (
                [solar_entities] if isinstance(solar_entities, str) else solar_entities or []
            )
            solar_w = sum(self._measured_power_w(entity_id, now) for entity_id in solar_ids)
            battery_w = self._battery_power_w(now)
            export_entity = self.config.get(CONF_GRID_EXPORT_SENSOR)
            export_w = (
                self._measured_power_w(export_entity, now)
                if export_entity and not grid_power_is_net(house_id) else 0.0
            )
            house_w = house_from_grid_w(house_w, solar_w, battery_w, export_w)
        ev_entities = self.config.get(CONF_EV_POWER_ENTITIES) or self.config.get(
            CONF_EV_POWER_ENTITY
        )
        ev_w = self._sum_power_w(ev_entities)
        total_house_w = (
            house_w if self.config[CONF_HOUSE_LOAD_INCLUDES_EV] else house_w + ev_w
        )
        self.power_sources = {
            "house_entity": house_id, "house_is_grid": house_is_grid,
            "house_inverted": house_inverted, "house_consumption_w": round(total_house_w, 1),
            "battery_net_entity": self.config.get(CONF_BATTERY_POWER_ENTITY),
            "battery_direction": self.config[CONF_BATTERY_POWER_DIRECTION],
            "observed_grid_charge_w": self._previous_grid_charge_w(now),
        }
        self.storage.model.observe(now, total_house_w, solar_w, ev_w)
        forecasts = self.storage.model.forecast(slots)

        force_action = await self.controller.async_active_force_action(now)
        plan_action = (
            None
            if force_action is not None
            else await self.controller.async_active_plan_action(now)
        )
        decision = optimize(
            OptimizerInput(
                now=now,
                soc_pct=self._value(self.config[CONF_SOC_ENTITY]),
                phase_currents_a=currents,  # type: ignore[arg-type]
                price_slots=slots,
                forecasts=forecasts,
                battery_capacity_kwh=self.config[CONF_BATTERY_CAPACITY_KWH],
                min_soc_pct=self.config[CONF_MIN_SOC_PCT],
                max_target_soc_pct=self.config[CONF_MAX_TARGET_SOC_PCT],
                max_charge_power_w=self.config[CONF_MAX_CHARGE_POWER_W],
                max_discharge_power_w=self.config[CONF_MAX_DISCHARGE_POWER_W],
                main_fuse_a=self.config[CONF_MAIN_FUSE_A],
                phase_safety_margin_a=self.config[CONF_PHASE_SAFETY_MARGIN_A],
                nominal_voltage_v=self.config[CONF_NOMINAL_VOLTAGE_V],
                round_trip_efficiency=self.config[CONF_ROUND_TRIP_EFFICIENCY],
                expensive_quantile=self.config[CONF_EXPENSIVE_QUANTILE],
                min_savings_percent=self.config[CONF_MIN_SAVINGS_PERCENT],
                cycle_cost=self.config[CONF_CYCLE_COST],
                previous_charge_power_w=self._previous_grid_charge_w(now),
                discharge_enabled=self.controller.discharge_enabled,
                force_action=force_action or plan_action,
                plan_overrides=self.controller.plan_overrides,
                winter_active=self.config[CONF_SWEDISH_WINTER_MODE]
                and _is_swedish_winter(now.month, now.day),
                winter_solar_factor=self.config[CONF_WINTER_SOLAR_FACTOR],
                outdoor_temperature_c=self._value(
                    self.config.get(CONF_OUTDOOR_TEMPERATURE_ENTITY), 8.0
                ),
                heating_balance_temp_c=self.config[CONF_HEATING_BALANCE_TEMP_C],
                heating_reserve_kw_per_c=self.config[
                    CONF_HEATING_RESERVE_KW_PER_C
                ],
                capacity_tariff_enabled=self.config[CONF_CAPACITY_TARIFF_ENABLED],
                capacity_tariff_model=self.config[CONF_CAPACITY_TARIFF_MODEL],
                capacity_tariff_rate_per_kw=self.config[
                    CONF_CAPACITY_TARIFF_RATE_PER_KW
                ],
                capacity_tariff_reference_kw=self.config[
                    CONF_CAPACITY_TARIFF_REFERENCE_KW
                ],
                capacity_tariff_start_hour=self.config[
                    CONF_CAPACITY_TARIFF_START_HOUR
                ],
                capacity_tariff_end_hour=self.config[CONF_CAPACITY_TARIFF_END_HOUR],
            )
        )
        decision = replace(
            decision,
            phase_data_age_seconds=round(phase_data_age, 1),
        )
        if plan_action is not None:
            plan_reasons = {
                "manual_force_charge": "manual_plan_charge",
                "manual_force_idle": "manual_plan_idle",
            }
            if decision.reason in plan_reasons:
                decision = replace(
                    decision,
                    reason=plan_reasons[decision.reason],
                )
        try:
            override = await self.controller.async_apply(decision)
        except Exception as err:
            _LOGGER.exception("Could not safely apply the battery decision")
            return replace(
                decision,
                action="blocked",
                charge_power_w=0,
                discharge_power_w=0,
                reason=(
                    str(err).split(" ", 1)[0] if isinstance(err, LiveSafetyError)
                    else "power_write_unconfirmed"
                    if isinstance(err, PowerReadbackError)
                    else "control_write_failed"
                ),
                control_error=f"{type(err).__name__}: {err}",
            )
        if override:
            decision = replace(
                decision,
                action="blocked",
                charge_power_w=0,
                discharge_power_w=0,
                reason=override,
            )
        if not self.controller.enabled:
            await self.storage.async_save()
        return decision

