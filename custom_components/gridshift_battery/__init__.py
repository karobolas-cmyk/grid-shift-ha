"""GridShift Battery Optimizer integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_KEY_PREFIX, STORAGE_VERSION, is_configured
from .controller import BatteryController, RuntimeStorage
from .coordinator import GridShiftCoordinator, merged_config

PLATFORMS = [Platform.SENSOR, Platform.SWITCH]


@dataclass(slots=True)
class GridShiftRuntime:
    """Runtime objects owned by one config entry."""

    coordinator: GridShiftCoordinator | None
    controller: BatteryController | None
    storage: RuntimeStorage
    platforms_loaded: bool = False


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one GridShift instance."""
    store: Store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}{entry.entry_id}")
    storage = RuntimeStorage(store, await store.async_load())
    if not is_configured(dict(entry.data)):
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = GridShiftRuntime(
            None, None, storage
        )
        entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
        return True

    controller = BatteryController(hass, merged_config(entry), storage)
    coordinator = GridShiftCoordinator(hass, entry, storage, controller)
    runtime = GridShiftRuntime(coordinator, controller, storage, True)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload and safely relinquish battery control."""
    runtime: GridShiftRuntime = hass.data[DOMAIN][entry.entry_id]
    if runtime.controller is not None and runtime.controller.enabled:
        await runtime.controller.async_release_control()
        await runtime.storage.async_save()
    unload_ok = True
    if runtime.platforms_loaded:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
