"""Recommendation and diagnostic sensors for GridShift."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import GridShiftRuntime
from .const import DOMAIN
from .models import Decision


@dataclass(frozen=True, kw_only=True)
class GridShiftSensorDescription(SensorEntityDescription):
    """Describe one value extracted from a decision."""

    value_fn: Callable[[Decision], Any]


DESCRIPTIONS = (
    GridShiftSensorDescription(
        key="action", name="Action", icon="mdi:battery-clock", value_fn=lambda item: item.action
    ),
    GridShiftSensorDescription(
        key="reason",
        name="Decision reason",
        icon="mdi:text-box-search",
        value_fn=lambda item: item.reason,
    ),
    GridShiftSensorDescription(
        key="recommended_charge_power",
        name="Recommended charge power",
        icon="mdi:battery-charging",
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda item: item.charge_power_w,
    ),
    GridShiftSensorDescription(
        key="recommended_discharge_power",
        name="Recommended grid discharge power",
        icon="mdi:transmission-tower-export",
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda item: item.discharge_power_w,
    ),
    GridShiftSensorDescription(
        key="phase_headroom",
        name="Minimum phase headroom",
        icon="mdi:sine-wave",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        value_fn=lambda item: item.phase_headroom_a,
    ),
    GridShiftSensorDescription(
        key="phase_power_limit",
        name="Phase-limited charge power",
        icon="mdi:transmission-tower",
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda item: item.phase_limit_w,
    ),
    GridShiftSensorDescription(
        key="phase_data_age",
        name="Oldest phase sensor age",
        icon="mdi:timer-alert-outline",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_display_precision=0,
        value_fn=lambda item: item.phase_data_age_seconds,
    ),
    GridShiftSensorDescription(
        key="adaptive_target_soc",
        name="Adaptive target state of charge",
        icon="mdi:battery-sync",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        value_fn=lambda item: item.adaptive_target_soc_pct,
    ),
    GridShiftSensorDescription(
        key="energy_needed",
        name="Grid energy needed",
        icon="mdi:meter-electric",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda item: item.energy_needed_kwh,
    ),
    GridShiftSensorDescription(
        key="forecast_expensive_load",
        name="Forecast expensive-period load",
        icon="mdi:home-lightning-bolt",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda item: item.forecast_expensive_load_kwh,
    ),
    GridShiftSensorDescription(
        key="forecast_ev_energy",
        name="Forecast EV energy",
        icon="mdi:ev-station",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda item: item.forecast_ev_energy_kwh,
    ),
    GridShiftSensorDescription(
        key="forecast_solar_energy",
        name="Forecast solar energy",
        icon="mdi:solar-power",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda item: item.forecast_solar_energy_kwh,
    ),
    GridShiftSensorDescription(
        key="heating_reserve",
        name="Swedish winter heating reserve",
        icon="mdi:radiator",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda item: item.heating_reserve_kwh,
    ),
    GridShiftSensorDescription(
        key="price_percentile",
        name="Current price percentile",
        icon="mdi:chart-bell-curve-cumulative",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        value_fn=lambda item: item.price_percentile,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create sensors for one config entry."""
    runtime: GridShiftRuntime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        GridShiftSensor(runtime, entry, description) for description in DESCRIPTIONS
    )


class GridShiftSensor(CoordinatorEntity, SensorEntity):
    """Expose one field from the latest optimizer decision."""

    entity_description: GridShiftSensorDescription
    _unrecorded_attributes = frozenset({
        "plan", "control_execution", "market_prices", "power_snapshot", "thermal_control",
        "notifications",
    })

    def __init__(
        self,
        runtime: GridShiftRuntime,
        entry: ConfigEntry,
        description: GridShiftSensorDescription,
    ) -> None:
        super().__init__(runtime.coordinator)
        self.runtime = runtime
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_has_entity_name = True
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="GridShift",
            model="Local battery optimizer",
        )

    @property
    def native_value(self):
        """Return the latest field value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Add the planned cheap slots to the action entity."""
        if self.entity_description.key != "action":
            return None
        data = self.coordinator.data
        return {
            "bridge_version": "0.14.7",
            "capabilities": [
                "house_consumption_entity", "market_prices", "power_snapshot", "notifications",
                "persistent_control", "climate_adjustment_policy", "climate_power_measurement",
            ],
            "selected_slots": list(data.selected_slots),
            "current_price": data.current_price,
            "cheap_cutoff": data.cheap_cutoff,
            "expensive_reference": data.expensive_reference,
            "discharge_power_w": data.discharge_power_w,
            "stale_phase_entities": list(data.stale_phase_entities),
            "forced_action": self.runtime.controller.forced_action,
            "forced_until": self.runtime.controller.forced_until,
            "plan_overrides": self.runtime.controller.plan_overrides,
            "control_enabled": self.runtime.controller.enabled,
            "storage_control_mode": self.runtime.controller.current_mode,
            "last_charge_power_w": self.runtime.controller.last_power_w,
            "last_discharge_power_w": self.runtime.controller.last_discharge_power_w,
            "control_error": data.control_error,
            "control_execution": dict(self.runtime.controller.execution),
            "control_paused_reason": self.runtime.controller.pause_reason,
            "control_recovery": self.runtime.controller.recovery,
            "plan": data.plan,
            "charge_power_w": data.charge_power_w,
            "adaptive_target_soc_pct": data.adaptive_target_soc_pct,
            "energy_needed_kwh": data.energy_needed_kwh,
            "phase_limit_w": data.phase_limit_w,
            "phase_headroom_a": data.phase_headroom_a,
            "later_reserve_kwh": data.later_reserve_kwh,
            "available_for_home_kwh": data.available_for_home_kwh,
            "home_demand_kwh": data.home_demand_kwh,
            "cheap_recharge_available": data.cheap_recharge_available,
            "power_sources": getattr(self.coordinator, "power_sources", {}),
            "power_snapshot": self.coordinator.power_snapshot,
            "market_prices": self.coordinator.prices.snapshot,
            "thermal_control": self.coordinator.thermal.snapshot,
            "notifications": self.coordinator.notifications.snapshot,
        }

