"""Opt-in control switch for GridShift."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import GridShiftRuntime
from .const import CONF_DISCHARGE_POWER_ENTITY, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the automation switch."""
    runtime: GridShiftRuntime = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = [GridShiftControlSwitch(runtime, entry)]
    if runtime.controller.config.get(CONF_DISCHARGE_POWER_ENTITY):
        entities.append(GridShiftDischargeSwitch(runtime, entry))
    async_add_entities(entities)


class GridShiftControlSwitch(CoordinatorEntity, SwitchEntity):
    """Enable writes only after the user explicitly opts in."""

    _attr_has_entity_name = True
    _attr_name = "Automatic battery control"
    _attr_icon = "mdi:battery-sync"

    def __init__(self, runtime: GridShiftRuntime, entry: ConfigEntry) -> None:
        super().__init__(runtime.coordinator)
        self.runtime = runtime
        self._attr_unique_id = f"{entry.entry_id}_control_enabled"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="GridShift",
            model="Local battery optimizer",
        )

    @property
    def available(self) -> bool:
        """Keep the switch available so control can always be disabled."""
        return True

    @property
    def is_on(self) -> bool:
        return self.runtime.controller.enabled

    async def async_turn_on(self, **kwargs) -> None:
        await self.runtime.controller.async_set_enabled(True)
        await self.runtime.coordinator.async_request_refresh()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.runtime.controller.async_set_enabled(False)
        self.async_write_ha_state()


class GridShiftDischargeSwitch(CoordinatorEntity, SwitchEntity):
    """Explicitly arm profitable battery discharge to the grid."""

    _attr_has_entity_name = True
    _attr_name = "Allow discharge to grid"
    _attr_icon = "mdi:transmission-tower-export"

    def __init__(self, runtime: GridShiftRuntime, entry: ConfigEntry) -> None:
        super().__init__(runtime.coordinator)
        self.runtime = runtime
        self._attr_unique_id = f"{entry.entry_id}_discharge_to_grid_enabled"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="GridShift",
            model="Local battery optimizer",
        )

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.runtime.controller.discharge_enabled

    async def async_turn_on(self, **kwargs) -> None:
        await self.runtime.controller.async_set_discharge_enabled(True)
        await self.runtime.coordinator.async_request_refresh()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.runtime.controller.async_set_discharge_enabled(False)
        await self.runtime.coordinator.async_request_refresh()
        self.async_write_ha_state()
