"""Safe actuator for the entities exposed by callifo/fronius_modbus."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any
from uuid import uuid4

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    CONF_AUTO_MODE_OPTION,
    CONF_BLOCK_DISCHARGE_MODE_OPTION,
    CONF_CHARGE_MODE_OPTION,
    CONF_CHARGE_POWER_ENTITY,
    CONF_CONTROL_MODE_ENTITY,
    CONF_DISCHARGE_MODE_OPTION,
    CONF_DISCHARGE_POWER_ENTITY,
    CONF_MODE_SETTLE_SECONDS,
)
from .learning import ConsumptionModel
from .models import Decision

_LOGGER = logging.getLogger(__name__)
POWER_READBACK_TIMEOUT_SECONDS = 30.0
POWER_READBACK_POLL_SECONDS = 0.5
POWER_READBACK_TOLERANCE_W = 5.0
POWER_ENTITY_TIMEOUT_SECONDS = 60.0
POWER_WRITE_MAX_ATTEMPTS = 3
POWER_WRITE_RETRY_SECONDS = 5.0


class PowerReadbackError(RuntimeError):
    """The requested setpoint was not observed after a successful service call."""


class LiveSafetyError(RuntimeError):
    """Fresh safety inputs temporarily forbid a power command."""


@dataclass(slots=True)
class PersistedState:
    """State stored locally by Home Assistant."""

    enabled: bool = False
    owns_control: bool = False
    discharge_enabled: bool = False
    forced_action: str | None = None
    forced_until: str | None = None
    plan_overrides: dict[str, str] = field(default_factory=dict)
    control_fault: dict[str, Any] = field(default_factory=dict)
    price_cache: dict[str, Any] = field(default_factory=dict)
    thermal_enabled: bool = False
    thermal_state: dict[str, Any] = field(default_factory=dict)
    notification_settings: dict[str, Any] = field(default_factory=dict)
    notification_state: dict[str, Any] = field(default_factory=dict)


class RuntimeStorage:
    """Serialize controller and learning state through one HA Store."""

    def __init__(self, store: Store[dict[str, Any]], raw: dict[str, Any] | None) -> None:
        self._store = store
        raw = raw or {}
        self.state = PersistedState(
            enabled=bool(raw.get("enabled", False)),
            owns_control=bool(raw.get("owns_control", False)),
            discharge_enabled=bool(raw.get("discharge_enabled", False)),
            forced_action=raw.get("forced_action"),
            forced_until=raw.get("forced_until"),
            plan_overrides=dict(raw.get("plan_overrides") or {}),
            control_fault=dict(raw.get("control_fault") or {}),
            price_cache=dict(raw.get("price_cache") or {}),
            thermal_enabled=bool(raw.get("thermal_enabled", False)),
            thermal_state=dict(raw.get("thermal_state") or {}),
            notification_settings=dict(raw.get("notification_settings") or {}),
            notification_state=dict(raw.get("notification_state") or {}),
        )
        if self.state.control_fault:
            self.state.enabled = False
        self.model = ConsumptionModel.from_dict(raw.get("model"))

    async def async_save(self) -> None:
        """Persist the complete runtime state."""
        await self._store.async_save(
            {
                "enabled": self.state.enabled,
                "owns_control": self.state.owns_control,
                "discharge_enabled": self.state.discharge_enabled,
                "forced_action": self.state.forced_action,
                "forced_until": self.state.forced_until,
                "plan_overrides": self.state.plan_overrides,
                "control_fault": self.state.control_fault,
                "price_cache": self.state.price_cache,
                "thermal_enabled": self.state.thermal_enabled,
                "thermal_state": self.state.thermal_state,
                "notification_settings": self.state.notification_settings,
                "notification_state": self.state.notification_state,
                "model": self.model.to_dict(),
            }
        )


class BatteryController:
    """Apply decisions using the ordering required by fronius_modbus."""

    def __init__(
        self, hass: HomeAssistant, config: dict[str, Any], storage: RuntimeStorage
    ) -> None:
        self.hass = hass
        self.config = config
        self.storage = storage
        self.last_power_w = 0
        self.last_discharge_power_w = 0
        self.execution: dict[str, Any] = dict(
            storage.state.control_fault.get("execution") or {}
        )
        self._apply_lock = asyncio.Lock()
        self._suspended = False
        self.power_guard: Callable[[int, str], int] | None = None

    @property
    def enabled(self) -> bool:
        return self.storage.state.enabled

    @property
    def _can_write(self) -> bool:
        return self.enabled and not self._suspended

    async def async_prepare_reload(self) -> None:
        """Quiesce in-flight writes without changing the user's persisted opt-in."""
        self._suspended = True
        async with self._apply_lock:
            if self.storage.state.enabled or self.storage.state.owns_control:
                await self.async_release_control()

    def resume_after_failed_reload(self) -> None:
        """Let the existing worker continue if HA could not unload its platforms."""
        self._suspended = False

    @property
    def pause_reason(self) -> str | None:
        """Return the latched write fault, cleared only by explicit re-enabling."""
        return self.storage.state.control_fault.get("reason")

    @property
    def pause_error(self) -> str | None:
        """Return the original write error while control is paused."""
        return self.storage.state.control_fault.get("error")

    @property
    def discharge_enabled(self) -> bool:
        """Return whether profitable grid export is explicitly armed."""
        return self.storage.state.discharge_enabled

    @property
    def current_mode(self) -> str | None:
        """Return the physical Fronius storage-control mode."""
        return self._mode()

    @property
    def forced_action(self) -> str | None:
        """Return the currently stored manual action."""
        return self.storage.state.forced_action

    @property
    def forced_until(self) -> str | None:
        """Return the stored expiry timestamp."""
        return self.storage.state.forced_until

    @property
    def plan_overrides(self) -> dict[str, str]:
        """Return a copy of the persisted quarter-hour overrides."""
        return dict(self.storage.state.plan_overrides)

    def _clear_force_state(self) -> None:
        self.storage.state.forced_action = None
        self.storage.state.forced_until = None

    async def async_set_enabled(self, enabled: bool) -> None:
        """Enable control, or safely hand control back to the inverter."""
        self.storage.state.enabled = enabled
        if enabled:
            self.storage.state.control_fault = {}
        if not enabled:
            self._clear_force_state()
            await self.async_release_control()
        await self.storage.async_save()

    async def async_set_force_action(
        self, action: str, duration_minutes: int
    ) -> None:
        """Persist a short manual action; safety remains inside the optimizer."""
        if action not in {"charge", "discharge", "idle"}:
            raise ValueError(f"Unsupported force action: {action}")
        duration = max(1, min(15, int(duration_minutes)))
        self.storage.state.forced_action = action
        self.storage.state.forced_until = (
            datetime.now(UTC) + timedelta(minutes=duration)
        ).isoformat()
        await self.storage.async_save()

    async def async_clear_force_action(self) -> None:
        """Clear a manual action and return the next cycle to the price plan."""
        self._clear_force_state()
        await self.storage.async_save()

    async def async_set_plan_overrides(self, overrides: dict[str, str]) -> None:
        """Replace persisted quarter-hour actions submitted by GridShift."""
        now = datetime.now(UTC)
        earliest = now - timedelta(minutes=15)
        latest = now + timedelta(hours=48)
        normalized: dict[str, str] = {}
        for start_text, action in list(overrides.items())[:192]:
            if action not in {"charge", "idle"}:
                raise ValueError(f"Unsupported plan action: {action}")
            try:
                start = datetime.fromisoformat(str(start_text))
            except ValueError as err:
                raise ValueError(f"Invalid plan timestamp: {start_text}") from err
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
            start = start.astimezone(UTC)
            if earliest <= start <= latest:
                normalized[start.isoformat()] = action
        self.storage.state.plan_overrides = normalized
        await self.storage.async_save()

    async def async_active_plan_action(self, now: datetime) -> str | None:
        """Return the current persisted quarter action and prune old entries."""
        utc_now = now.astimezone(UTC)
        active: str | None = None
        retained: dict[str, str] = {}
        changed = False
        for start_text, action in self.storage.state.plan_overrides.items():
            try:
                start = datetime.fromisoformat(start_text)
            except ValueError:
                changed = True
                continue
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
            start = start.astimezone(UTC)
            end = start + timedelta(minutes=15)
            if end <= utc_now:
                changed = True
                continue
            retained[start.isoformat()] = action
            if start <= utc_now < end:
                active = action
        if changed or retained != self.storage.state.plan_overrides:
            self.storage.state.plan_overrides = retained
            await self.storage.async_save()
        return active

    async def async_active_force_action(self, now: datetime) -> str | None:
        """Return an unexpired manual action, clearing stale state."""
        action = self.storage.state.forced_action
        until_text = self.storage.state.forced_until
        if not action or not until_text:
            return None
        try:
            until = datetime.fromisoformat(until_text)
        except ValueError:
            until = now
        if until.tzinfo is None:
            until = until.replace(tzinfo=UTC)
        if now.astimezone(UTC) >= until.astimezone(UTC):
            self._clear_force_state()
            await self.storage.async_save()
            return None
        return action

    async def async_set_discharge_enabled(self, enabled: bool) -> None:
        """Arm grid discharge, or return an active discharge to Auto."""
        if not enabled and self._mode() == self.config[CONF_DISCHARGE_MODE_OPTION]:
            await self.async_release_control()
        if not enabled and self.storage.state.forced_action == "discharge":
            self._clear_force_state()
        self.storage.state.discharge_enabled = enabled
        await self.storage.async_save()

    def _mode(self) -> str | None:
        state = self.hass.states.get(self.config[CONF_CONTROL_MODE_ENTITY])
        return state.state if state else None

    async def _set_mode(self, option: str) -> None:
        await self.hass.services.async_call(
            "select",
            "select_option",
            {
                "entity_id": self.config[CONF_CONTROL_MODE_ENTITY],
                "option": option,
            },
            blocking=True,
        )

    async def _set_power(self, entity_id: str, power_w: int) -> int:
        """Retry a transient rate failure without toggling the storage mode."""
        self.execution["write_attempts"] = []
        for attempt in range(1, POWER_WRITE_MAX_ATTEMPTS + 1):
            self.execution["write_attempt"] = attempt
            try:
                return await self._set_power_once(entity_id, power_w)
            except Exception as err:
                self.execution["write_attempts"].append(
                    {
                        "attempt": attempt,
                        "stage": self.execution["stage"],
                        "at": datetime.now(UTC).isoformat(),
                        "error": f"{type(err).__name__}: {err}",
                        "readback_power_w": self.execution.get("readback_power_w"),
                    }
                )
                transient = isinstance(err, PowerReadbackError) or any(
                    phrase in str(err).lower()
                    for phrase in (
                        "unavailable", "not available", "not ready", "busy",
                        "temporar", "timeout", "timed out", "current storage mode",
                        "can only be changed in",
                    )
                )
                if (
                    attempt >= POWER_WRITE_MAX_ATTEMPTS
                    or not transient
                    or not self._can_write
                    or self._mode() != self.execution["requested_mode"]
                ):
                    raise
                _LOGGER.warning(
                    "Retrying power write %s on %s, attempt %s/%s: %s",
                    self.execution["command_id"], entity_id,
                    attempt + 1, POWER_WRITE_MAX_ATTEMPTS, err,
                )
                self.execution["stage"] = "retry_power_write"
                await asyncio.sleep(POWER_WRITE_RETRY_SECONDS)
                if not self._can_write or self._mode() != self.execution["requested_mode"]:
                    raise RuntimeError(
                        "Control was disabled or Fronius mode changed during retry"
                    ) from err
                await self._wait_for_power_entity(entity_id, self.execution["requested_mode"])
        raise RuntimeError("Power write attempts exhausted")

    async def _set_power_once(self, entity_id: str, power_w: int) -> int:
        self.execution.update(
            stage="check_live_safety", service_call_succeeded=False,
            power_write_confirmed=False,
        )
        if not self._can_write or self._mode() != self.execution["requested_mode"]:
            raise RuntimeError("Control was disabled or Fronius mode changed before rate write")
        guarded = (
            self.power_guard(power_w, self.execution["action"])
            if self.power_guard else power_w
        )
        rounded = int(guarded) // 10 * 10
        started = datetime.now(UTC)
        entity_state = self.hass.states.get(entity_id)
        attributes = getattr(entity_state, "attributes", {})
        self.execution.update(
            stage="write_power",
            power_entity=entity_id,
            commanded_power_w=rounded,
            previous_power_w=self._read_power(entity_id),
            write_started_at=started.isoformat(),
            service_call_succeeded=False,
            entity_min=attributes.get("min"),
            entity_max=attributes.get("max"),
            entity_step=attributes.get("step"),
            entity_unit=attributes.get("unit_of_measurement"),
        )
        await self.hass.services.async_call(
            "number",
            "set_value",
            {
                "entity_id": entity_id,
                "value": rounded,
            },
            blocking=True,
        )
        self.execution.update(
            stage="verify_power",
            service_call_succeeded=True,
            write_returned_at=datetime.now(UTC).isoformat(),
        )
        attempts = max(
            1, int(POWER_READBACK_TIMEOUT_SECONDS / POWER_READBACK_POLL_SECONDS)
        )
        for attempt in range(attempts + 1):
            state = self.hass.states.get(entity_id)
            observed = self._read_power(entity_id)
            reported_at = (
                getattr(state, "last_reported", None)
                or getattr(state, "last_updated", None)
            )
            fresh = isinstance(reported_at, datetime) and reported_at >= started
            self.execution.update(
                readback_power_w=observed,
                readback_at=reported_at.isoformat() if reported_at else None,
                readback_fresh=fresh,
            )
            if not self._can_write or self._mode() != self.execution["requested_mode"]:
                raise PowerReadbackError("Control was disabled or Fronius mode changed")
            if (
                fresh
                and observed is not None
                and abs(observed - rounded) <= POWER_READBACK_TOLERANCE_W
            ):
                self.execution["power_write_confirmed"] = True
                return rounded
            if attempt < attempts:
                await asyncio.sleep(POWER_READBACK_POLL_SECONDS)
        raise PowerReadbackError(
            f"Requested {rounded} W on {entity_id}; read back "
            f"{self.execution['readback_power_w']} W "
            f"(fresh after write: {self.execution['readback_fresh']})"
        )

    def _read_power(self, entity_id: str) -> float | None:
        """Return the observed setpoint, never a remembered command."""
        state = self.hass.states.get(entity_id)
        try:
            value = float(state.state) if state is not None else float("nan")
        except (TypeError, ValueError):
            return None
        return value if isfinite(value) else None

    async def _settle_mode(self, mode: str) -> None:
        """Select a Fronius mode, wait for it to settle, then verify it."""
        if self._mode() != mode:
            self.execution["stage"] = "select_mode"
            await self._set_mode(mode)
            self.execution["mode_selected_at"] = datetime.now(UTC).isoformat()
            self.execution["stage"] = "settle_mode"
            await asyncio.sleep(max(0, float(self.config[CONF_MODE_SETTLE_SECONDS])))
        else:
            state = self.hass.states.get(self.config[CONF_CONTROL_MODE_ENTITY])
            changed_at = getattr(state, "last_changed", None)
            if isinstance(changed_at, datetime):
                age = max(0.0, (datetime.now(UTC) - changed_at).total_seconds())
                remaining = max(0.0, float(self.config[CONF_MODE_SETTLE_SECONDS]) - age)
                if remaining:
                    self.execution["stage"] = "settle_mode"
                    await asyncio.sleep(remaining)
        if not self._can_write:
            raise RuntimeError("Control was disabled or Fronius mode changed before power write")
        if self._mode() != mode:
            raise RuntimeError(f"Fronius storage mode did not settle to {mode}")
        self.execution["mode_confirmed_at"] = datetime.now(UTC).isoformat()

    async def _wait_for_power_entity(self, entity_id: str, mode: str) -> None:
        """Wait for a mode-specific Fronius number to become writable."""
        poll_seconds = 0.5
        timeout_seconds = max(poll_seconds, POWER_ENTITY_TIMEOUT_SECONDS)
        attempts = max(1, int(timeout_seconds / poll_seconds))
        for attempt in range(attempts + 1):
            if not self._can_write or self._mode() != mode:
                raise RuntimeError("Control was disabled or Fronius mode changed while waiting")
            state = self.hass.states.get(entity_id)
            if state is not None and state.state not in ("unknown", "unavailable"):
                self.execution["power_entity_ready_at"] = datetime.now(UTC).isoformat()
                return
            if attempt < attempts:
                await asyncio.sleep(poll_seconds)
        raise RuntimeError(
            f"{entity_id} stayed unavailable for {timeout_seconds:g} seconds "
            f"in {mode} mode"
        )

    async def _enter_mode(self, mode: str, power_entity: str) -> None:
        """Enter a power-control mode and verify its mode-specific entity."""
        await self._settle_mode(mode)
        self.execution["stage"] = "wait_power_entity"
        await self._wait_for_power_entity(power_entity, mode)
        if not self._can_write or self._mode() != mode:
            raise RuntimeError("Control was disabled or Fronius mode changed before power write")

    async def async_apply(self, decision: Decision) -> str | None:
        """Apply a decision and return an override reason when control is refused."""
        async with self._apply_lock:
            return await self._async_apply_serialized(decision)

    async def _async_apply_serialized(self, decision: Decision) -> str | None:
        """Keep mode, power write and acknowledgement in one serialized attempt."""
        if not self._can_write:
            return self.pause_reason or ("control_reloading" if self._suspended else None)

        requested_modes = {
            "charge": self.config[CONF_CHARGE_MODE_OPTION],
            "discharge": self.config[CONF_DISCHARGE_MODE_OPTION],
            "hold": self.config[CONF_BLOCK_DISCHARGE_MODE_OPTION],
        }
        self.execution = {
            "command_id": uuid4().hex,
            "action": decision.action,
            "requested_mode": requested_modes.get(
                decision.action, self.config[CONF_AUTO_MODE_OPTION]
            ),
            "requested_power_w": (
                decision.discharge_power_w
                if decision.action == "discharge"
                else decision.charge_power_w
            ),
            "power_entity": self.config.get(
                CONF_DISCHARGE_POWER_ENTITY
                if decision.action == "discharge" else CONF_CHARGE_POWER_ENTITY
            ),
            "status": "pending",
            "stage": "check_mode",
            "started_at": datetime.now(UTC).isoformat(),
            "power_write_confirmed": False,
        }
        try:
            override = await self._async_apply_decision(decision)
        except Exception as err:
            self.execution.update(
                status="failed",
                failed_stage=self.execution["stage"],
                error=f"{type(err).__name__}: {err}",
            )
            was_enabled = self._can_write
            should_pause = was_enabled and not isinstance(err, LiveSafetyError)
            entity_id = self.config.get(
                CONF_DISCHARGE_POWER_ENTITY
                if decision.action == "discharge" else CONF_CHARGE_POWER_ENTITY
            )
            state = self.hass.states.get(entity_id) if entity_id else None
            reported_at = (
                getattr(state, "last_reported", None)
                or getattr(state, "last_updated", None)
            )
            started_at = datetime.fromisoformat(self.execution["started_at"])
            observed_power = self._read_power(entity_id) if entity_id else None
            fresh_zero = (
                decision.action in {"charge", "discharge"}
                and observed_power == 0
                and isinstance(reported_at, datetime)
                and reported_at >= started_at
            )
            self.execution["failure_observed_power_w"] = observed_power
            if fresh_zero and self._mode() == self.execution["requested_mode"]:
                self.execution["fallback_action"] = "retain_verified_zero_mode"
                self.execution["fallback_mode"] = self._mode()
                self.storage.state.owns_control = False
                self.last_power_w = 0
                self.last_discharge_power_w = 0
            elif was_enabled and self._mode() == self.execution["requested_mode"]:
                try:
                    await self.async_release_control()
                    self.execution["fallback_mode"] = self._mode()
                    self.execution["fallback_action"] = "release_unknown_or_nonzero_power_once"
                except Exception as release_err:
                    self.execution["fallback_error"] = (
                        f"{type(release_err).__name__}: {release_err}"
                    )
                    _LOGGER.exception("Could not return Fronius to Auto after an unverified write")
            self.execution["finished_at"] = datetime.now(UTC).isoformat()
            if should_pause:
                # Stop all automatic retries, including after a restart. A fresh
                # input update must not immediately restart the failed sequence.
                self.storage.state.enabled = False
                self._clear_force_state()
                self.execution["paused"] = True
                self.storage.state.control_fault = {
                    "reason": "control_paused_after_write_failure",
                    "error": self.execution["error"],
                    "execution": dict(self.execution),
                }
                try:
                    await self.storage.async_save()
                except Exception:
                    _LOGGER.exception("Could not persist the paused GridShift controller")
            raise
        self.execution.update(
            status="refused" if override else "confirmed",
            stage="complete",
            finished_at=datetime.now(UTC).isoformat(),
            observed_mode=self._mode(),
        )
        try:
            await self.storage.async_save()
        except Exception as err:
            # Persistence failure must not turn a verified device write into a
            # misleading write failure; report it separately for diagnostics.
            self.execution["persistence_error"] = f"{type(err).__name__}: {err}"
            _LOGGER.exception("Could not persist GridShift state after applying a decision")
        return override

    async def _async_apply_decision(self, decision: Decision) -> str | None:
        """Execute one attempt; only verified rates update the command history."""

        charge_entity = self.config[CONF_CHARGE_POWER_ENTITY]
        discharge_entity_id = self.config.get(CONF_DISCHARGE_POWER_ENTITY) or ""
        if (
            charge_entity == discharge_entity_id
            or "grid_discharge_power" in charge_entity.lower()
            or "grid_charge_power" in discharge_entity_id.lower()
        ):
            raise RuntimeError(
                f"invalid_power_entity_mapping: charge={charge_entity}, "
                f"discharge={discharge_entity_id}; review Grid charge power mapping"
            )

        auto = self.config[CONF_AUTO_MODE_OPTION]
        charging = self.config[CONF_CHARGE_MODE_OPTION]
        holding = self.config[CONF_BLOCK_DISCHARGE_MODE_OPTION]
        discharging = self.config[CONF_DISCHARGE_MODE_OPTION]
        discharge_entity = self.config.get(CONF_DISCHARGE_POWER_ENTITY)
        mode = self._mode()
        if mode in (None, "", "unknown", "unavailable"):
            # Integrations restore entities asynchronously after an HA restart.
            # Missing mode data is not a user selecting a different valid mode.
            return "control_mode_unavailable"
        allowed_modes = {auto, charging, holding}
        if self.discharge_enabled and discharge_entity:
            allowed_modes.add(discharging)
        if mode not in allowed_modes:
            # A user or another automation selected a different mode. Stop our
            # automation instead of fighting for control.
            self.storage.state.enabled = False
            self.storage.state.owns_control = False
            self.last_power_w = 0
            self.last_discharge_power_w = 0
            return "manual_mode_override"

        if decision.action == "charge" and decision.charge_power_w > 0:
            await self._enter_mode(charging, self.config[CONF_CHARGE_POWER_ENTITY])
            self.last_power_w = await self._set_power(
                self.config[CONF_CHARGE_POWER_ENTITY], decision.charge_power_w
            )
            self.last_discharge_power_w = 0
            self.storage.state.owns_control = True
        elif decision.action == "hold":
            await self._settle_mode(holding)
            self.last_power_w = 0
            self.last_discharge_power_w = 0
            self.storage.state.owns_control = True
        elif decision.action == "discharge" and decision.discharge_power_w > 0:
            if not self.discharge_enabled:
                await self.async_release_control()
                return "grid_discharge_not_enabled"
            if not discharge_entity:
                await self.async_release_control()
                return "grid_discharge_not_configured"
            await self._enter_mode(discharging, discharge_entity)
            self.last_discharge_power_w = await self._set_power(
                discharge_entity, decision.discharge_power_w
            )
            self.last_power_w = 0
            self.storage.state.owns_control = True
        else:
            self.execution["stage"] = "release_auto"
            await self.async_release_control()
            if self._mode() != auto:
                raise RuntimeError("Fronius storage mode did not return to Auto")
        return None

    async def async_release_control(self) -> None:
        """Return to Auto without writing a mode-specific power control."""
        charging = self.config[CONF_CHARGE_MODE_OPTION]
        holding = self.config[CONF_BLOCK_DISCHARGE_MODE_OPTION]
        discharging = self.config[CONF_DISCHARGE_MODE_OPTION]
        mode = self._mode()
        controlled_mode = mode in {charging, holding} or (
            mode == discharging
            and (self.storage.state.owns_control or self.discharge_enabled)
        )
        if self.storage.state.owns_control or controlled_mode:
            await self._set_mode(self.config[CONF_AUTO_MODE_OPTION])
        self.storage.state.owns_control = False
        self.last_power_w = 0
        self.last_discharge_power_w = 0

