"""Safe actuator for the entities exposed by callifo/fronius_modbus."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

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


@dataclass(slots=True)
class PersistedState:
    """State stored locally by Home Assistant."""

    enabled: bool = False
    owns_control: bool = False
    discharge_enabled: bool = False


class RuntimeStorage:
    """Serialize controller and learning state through one HA Store."""

    def __init__(self, store: Store[dict[str, Any]], raw: dict[str, Any] | None) -> None:
        self._store = store
        raw = raw or {}
        self.state = PersistedState(
            enabled=bool(raw.get("enabled", False)),
            owns_control=bool(raw.get("owns_control", False)),
            discharge_enabled=bool(raw.get("discharge_enabled", False)),
        )
        self.model = ConsumptionModel.from_dict(raw.get("model"))

    async def async_save(self) -> None:
        """Persist the complete runtime state."""
        await self._store.async_save(
            {
                "enabled": self.state.enabled,
                "owns_control": self.state.owns_control,
                "discharge_enabled": self.state.discharge_enabled,
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

    @property
    def enabled(self) -> bool:
        return self.storage.state.enabled

    @property
    def discharge_enabled(self) -> bool:
        """Return whether profitable grid export is explicitly armed."""
        return self.storage.state.discharge_enabled

    async def async_set_enabled(self, enabled: bool) -> None:
        """Enable control, or safely hand control back to the inverter."""
        self.storage.state.enabled = enabled
        if not enabled:
            await self.async_release_control()
        await self.storage.async_save()

    async def async_set_discharge_enabled(self, enabled: bool) -> None:
        """Arm grid discharge, or return an active discharge to Auto."""
        if not enabled and self._mode() == self.config[CONF_DISCHARGE_MODE_OPTION]:
            await self.async_release_control()
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
        rounded = int(power_w) // 10 * 10
        await self.hass.services.async_call(
            "number",
            "set_value",
            {
                "entity_id": entity_id,
                "value": rounded,
            },
            blocking=True,
        )
        return rounded

    async def _settle_mode(self, mode: str) -> None:
        """Select a Fronius mode, wait for it to settle, then verify it."""
        if self._mode() != mode:
            await self._set_mode(mode)
            await asyncio.sleep(max(0, float(self.config[CONF_MODE_SETTLE_SECONDS])))
        if self._mode() != mode:
            raise RuntimeError(f"Fronius storage mode did not settle to {mode}")

    async def _enter_mode(self, mode: str, power_entity: str) -> None:
        """Enter a power-control mode and verify its mode-specific entity."""
        await self._settle_mode(mode)
        state = self.hass.states.get(power_entity)
        if state is None or state.state in ("unknown", "unavailable"):
            raise RuntimeError(f"{power_entity} is unavailable in {mode} mode")

    async def async_apply(self, decision: Decision) -> str | None:
        """Apply a decision and return an override reason when control is refused."""
        if not self.enabled:
            return None

        auto = self.config[CONF_AUTO_MODE_OPTION]
        charging = self.config[CONF_CHARGE_MODE_OPTION]
        holding = self.config[CONF_BLOCK_DISCHARGE_MODE_OPTION]
        discharging = self.config[CONF_DISCHARGE_MODE_OPTION]
        discharge_entity = self.config.get(CONF_DISCHARGE_POWER_ENTITY)
        mode = self._mode()
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
            await self.storage.async_save()
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
            await self.async_release_control()
        await self.storage.async_save()
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
