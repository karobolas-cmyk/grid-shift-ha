"""GridShift Battery Optimizer integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store

from .bridge import (
    ATTR_CONFIGURATION,
    SERVICE_APPLY_CONFIGURATION,
    validate_configuration,
)
from .const import (
    CONF_PHASE_CURRENT_ENTITIES,
    DOMAIN,
    SERVICE_CLEAR_FORCE_ACTION,
    SERVICE_FORCE_ACTION,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
    is_configured,
)
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


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register the narrow service used by the GridShift application."""

    async def async_apply_configuration(call: ServiceCall) -> None:
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            raise HomeAssistantError("Add the GridShift Bridge integration first")
        if len(entries) != 1:
            raise HomeAssistantError("Multiple GridShift Bridge entries are not supported")
        entry = entries[0]
        validated = validate_configuration(hass, call.data[ATTR_CONFIGURATION])
        runtime: GridShiftRuntime | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if runtime and runtime.controller and runtime.controller.enabled:
            await runtime.controller.async_set_enabled(False)
        hass.config_entries.async_update_entry(entry, data=validated, options={})

    async def async_force_action(call: ServiceCall) -> None:
        entries = hass.config_entries.async_entries(DOMAIN)
        if len(entries) != 1:
            raise HomeAssistantError("Exactly one configured GridShift entry is required")
        runtime: GridShiftRuntime | None = hass.data.get(DOMAIN, {}).get(
            entries[0].entry_id
        )
        if runtime is None or runtime.controller is None or runtime.coordinator is None:
            raise HomeAssistantError("Configure the GridShift Bridge before using live control")
        if not runtime.controller.enabled:
            raise HomeAssistantError("Enable Automatic battery control first")
        action = call.data["action"]
        if action == "discharge" and not runtime.controller.discharge_enabled:
            raise HomeAssistantError("Enable Allow discharge to grid first")
        await runtime.controller.async_set_force_action(
            action, call.data["duration_minutes"]
        )
        await runtime.coordinator.async_request_refresh()

    async def async_clear_force_action(call: ServiceCall) -> None:
        entries = hass.config_entries.async_entries(DOMAIN)
        if len(entries) != 1:
            raise HomeAssistantError("Exactly one configured GridShift entry is required")
        runtime: GridShiftRuntime | None = hass.data.get(DOMAIN, {}).get(
            entries[0].entry_id
        )
        if runtime is None or runtime.controller is None or runtime.coordinator is None:
            raise HomeAssistantError("Configure the GridShift Bridge before using live control")
        await runtime.controller.async_clear_force_action()
        await runtime.coordinator.async_request_refresh()

    if not hass.services.has_service(DOMAIN, SERVICE_APPLY_CONFIGURATION):
        hass.services.async_register(
            DOMAIN,
            SERVICE_APPLY_CONFIGURATION,
            async_apply_configuration,
            schema=vol.Schema({vol.Required(ATTR_CONFIGURATION): dict}),
        )
    if not hass.services.has_service(DOMAIN, SERVICE_FORCE_ACTION):
        hass.services.async_register(
            DOMAIN,
            SERVICE_FORCE_ACTION,
            async_force_action,
            schema=vol.Schema(
                {
                    vol.Required("action"): vol.In(["charge", "discharge", "idle"]),
                    vol.Required("duration_minutes"): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=15)
                    ),
                }
            ),
        )
    if not hass.services.has_service(DOMAIN, SERVICE_CLEAR_FORCE_ACTION):
        hass.services.async_register(
            DOMAIN,
            SERVICE_CLEAR_FORCE_ACTION,
            async_clear_force_action,
            schema=vol.Schema({}),
        )
    return True


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

    @callback
    def async_phase_sensor_changed(event: Event) -> None:
        """Re-evaluate immediately when stale or loaded phase data changes."""
        hass.async_create_task(coordinator.async_request_refresh())

    entry.async_on_unload(
        async_track_state_change_event(
            hass,
            controller.config[CONF_PHASE_CURRENT_ENTITIES],
            async_phase_sensor_changed,
        )
    )
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
