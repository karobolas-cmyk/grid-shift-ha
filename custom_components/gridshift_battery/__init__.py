"""GridShift Battery Optimizer integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_KEY_PREFIX, STORAGE_VERSION
from .controller import BatteryController, RuntimeStorage
from .coordinator import GridShiftCoordinator, merged_config

PLATFORMS = [Platform.SENSOR, Platform.SWITCH]


@dataclass(slots=True)
class GridShiftRuntime:
    """Runtime objects owned by one config entry."""

    coordinator: GridShiftCoordinator
    controller: BatteryController
    storage: RuntimeStorage


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one GridShift instance."""
    store: Store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}{entry.entry_id}")
    storage = RuntimeStorage(store, await store.async_load())
    controller = BatteryController(hass, merged_config(entry), storage)
    coordinator = GridShiftCoordinator(hass, entry, storage, controller)
    runtime = GridShiftRuntime(coordinator, controller, storage)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload and safely relinquish battery control."""
    runtime: GridShiftRuntime = hass.data[DOMAIN][entry.entry_id]
    if runtime.controller.enabled:
        await runtime.controller.async_release_control()
        await runtime.storage.async_save()
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
