"""Validated configuration bridge between GridShift and Home Assistant."""

from __future__ import annotations

import math
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_AUTO_MODE_OPTION,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BLOCK_DISCHARGE_MODE_OPTION,
    CONF_CAPACITY_TARIFF_ENABLED,
    CONF_CAPACITY_TARIFF_END_HOUR,
    CONF_CAPACITY_TARIFF_MODEL,
    CONF_CAPACITY_TARIFF_RATE_PER_KW,
    CONF_CAPACITY_TARIFF_REFERENCE_KW,
    CONF_CAPACITY_TARIFF_START_HOUR,
    CONF_CHARGE_MODE_OPTION,
    CONF_CHARGE_POWER_ENTITY,
    CONF_CONTROL_MODE_ENTITY,
    CONF_CYCLE_COST,
    CONF_DISCHARGE_MODE_OPTION,
    CONF_DISCHARGE_POWER_ENTITY,
    CONF_EV_POWER_ENTITIES,
    CONF_EXPENSIVE_QUANTILE,
    CONF_HEATING_BALANCE_TEMP_C,
    CONF_HEATING_RESERVE_KW_PER_C,
    CONF_HORIZON_HOURS,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_HOUSE_LOAD_INCLUDES_EV,
    CONF_INTERVAL_MINUTES,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_MAIN_FUSE_A,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MAX_DISCHARGE_POWER_W,
    CONF_MAX_TARGET_SOC_PCT,
    CONF_MIN_SAVINGS_PERCENT,
    CONF_MIN_SOC_PCT,
    CONF_MODE_SETTLE_SECONDS,
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
    CONF_SWEDISH_WINTER_MODE,
    CONF_WINTER_SOLAR_FACTOR,
)
from .fronius import missing_control_modes

SERVICE_APPLY_CONFIGURATION = "apply_configuration"
ATTR_CONFIGURATION = "configuration"

ENTITY_FIELDS = {
    CONF_PRICE_ENTITY: "sensor",
    CONF_SOC_ENTITY: "sensor",
    CONF_HOUSE_LOAD_ENTITY: "sensor",
    CONF_CONTROL_MODE_ENTITY: "select",
    CONF_CHARGE_POWER_ENTITY: "number",
    CONF_DISCHARGE_POWER_ENTITY: "number",
    CONF_OUTDOOR_TEMPERATURE_ENTITY: "sensor",
}
ENTITY_LIST_FIELDS = {
    CONF_PHASE_CURRENT_ENTITIES: "sensor",
    CONF_SOLAR_POWER_ENTITIES: "sensor",
    CONF_EV_POWER_ENTITIES: "sensor",
}
REQUIRED_FIELDS = {
    CONF_PRICE_ENTITY,
    CONF_SOC_ENTITY,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_PHASE_CURRENT_ENTITIES,
    CONF_CONTROL_MODE_ENTITY,
    CONF_CHARGE_POWER_ENTITY,
}
SETTING_FIELDS = {
    CONF_AUTO_MODE_OPTION,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BLOCK_DISCHARGE_MODE_OPTION,
    CONF_CAPACITY_TARIFF_ENABLED,
    CONF_CAPACITY_TARIFF_END_HOUR,
    CONF_CAPACITY_TARIFF_MODEL,
    CONF_CAPACITY_TARIFF_RATE_PER_KW,
    CONF_CAPACITY_TARIFF_REFERENCE_KW,
    CONF_CAPACITY_TARIFF_START_HOUR,
    CONF_CHARGE_MODE_OPTION,
    CONF_CYCLE_COST,
    CONF_DISCHARGE_MODE_OPTION,
    CONF_EXPENSIVE_QUANTILE,
    CONF_HEATING_BALANCE_TEMP_C,
    CONF_HEATING_RESERVE_KW_PER_C,
    CONF_HORIZON_HOURS,
    CONF_HOUSE_LOAD_INCLUDES_EV,
    CONF_INTERVAL_MINUTES,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_MAIN_FUSE_A,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MAX_DISCHARGE_POWER_W,
    CONF_MAX_TARGET_SOC_PCT,
    CONF_MIN_SAVINGS_PERCENT,
    CONF_MIN_SOC_PCT,
    CONF_MODE_SETTLE_SECONDS,
    CONF_NOMINAL_VOLTAGE_V,
    CONF_PHASE_SAFETY_MARGIN_A,
    CONF_PHASE_SENSOR_MAX_AGE_SECONDS,
    CONF_ROUND_TRIP_EFFICIENCY,
    CONF_SOLAR_PEAK_POWER_KWP,
    CONF_SWEDISH_WINTER_MODE,
    CONF_WINTER_SOLAR_FACTOR,
}
ALLOWED_FIELDS = set(ENTITY_FIELDS) | set(ENTITY_LIST_FIELDS) | SETTING_FIELDS
BOOLEAN_FIELDS = {
    CONF_CAPACITY_TARIFF_ENABLED,
    CONF_HOUSE_LOAD_INCLUDES_EV,
    CONF_SWEDISH_WINTER_MODE,
}
STRING_FIELDS = {
    CONF_AUTO_MODE_OPTION,
    CONF_BLOCK_DISCHARGE_MODE_OPTION,
    CONF_CAPACITY_TARIFF_MODEL,
    CONF_CHARGE_MODE_OPTION,
    CONF_DISCHARGE_MODE_OPTION,
}
NUMBER_RANGES = {
    CONF_BATTERY_CAPACITY_KWH: (1, 200),
    CONF_CAPACITY_TARIFF_END_HOUR: (0, 24),
    CONF_CAPACITY_TARIFF_RATE_PER_KW: (0, 1000),
    CONF_CAPACITY_TARIFF_REFERENCE_KW: (0, 100),
    CONF_CAPACITY_TARIFF_START_HOUR: (0, 23),
    CONF_CYCLE_COST: (0, 10),
    CONF_EXPENSIVE_QUANTILE: (0.5, 0.9),
    CONF_HEATING_BALANCE_TEMP_C: (-10, 20),
    CONF_HEATING_RESERVE_KW_PER_C: (0, 2),
    CONF_HORIZON_HOURS: (6, 48),
    CONF_INTERVAL_MINUTES: (1, 60),
    CONF_LATITUDE: (-90, 90),
    CONF_LONGITUDE: (-180, 180),
    CONF_MAIN_FUSE_A: (6, 100),
    CONF_MAX_CHARGE_POWER_W: (100, 50000),
    CONF_MAX_DISCHARGE_POWER_W: (100, 50000),
    CONF_MAX_TARGET_SOC_PCT: (5, 100),
    CONF_MIN_SAVINGS_PERCENT: (0, 100),
    CONF_MIN_SOC_PCT: (0, 95),
    CONF_MODE_SETTLE_SECONDS: (0, 120),
    CONF_NOMINAL_VOLTAGE_V: (200, 250),
    CONF_PHASE_SAFETY_MARGIN_A: (0, 10),
    CONF_PHASE_SENSOR_MAX_AGE_SECONDS: (15, 900),
    CONF_ROUND_TRIP_EFFICIENCY: (0.5, 1),
    CONF_SOLAR_PEAK_POWER_KWP: (0, 1000),
    CONF_WINTER_SOLAR_FACTOR: (0, 1),
}


def _entity_id(value: Any, domain: str, field: str) -> str:
    if not isinstance(value, str) or not value.startswith(f"{domain}."):
        raise HomeAssistantError(f"{field} must be a {domain} entity")
    return value


def _entity_exists(hass: HomeAssistant, entity_id: str, field: str) -> None:
    if hass.states.get(entity_id) is None:
        raise HomeAssistantError(f"{field} references an entity that does not exist")


def validate_configuration(
    hass: HomeAssistant, raw: dict[str, Any]
) -> dict[str, Any]:
    """Validate and normalize a configuration submitted by GridShift."""
    if not isinstance(raw, dict):
        raise HomeAssistantError("configuration must be an object")
    unknown = set(raw) - ALLOWED_FIELDS
    if unknown:
        raise HomeAssistantError(f"unsupported configuration fields: {', '.join(sorted(unknown))}")
    missing = REQUIRED_FIELDS - set(raw)
    if missing:
        raise HomeAssistantError(f"missing configuration fields: {', '.join(sorted(missing))}")

    result = dict(raw)
    charge_entity = str(result.get(CONF_CHARGE_POWER_ENTITY, ""))
    discharge_entity = str(result.get(CONF_DISCHARGE_POWER_ENTITY, ""))
    if (
        charge_entity == discharge_entity
        or "grid_discharge_power" in charge_entity.lower()
        or "grid_charge_power" in discharge_entity.lower()
    ):
        raise HomeAssistantError(
            "charge_power_entity and discharge_power_entity must be distinct, "
            "with Grid charge power used only for charging"
        )
    for field, domain in ENTITY_FIELDS.items():
        value = result.get(field)
        if value in (None, "") and field not in REQUIRED_FIELDS:
            result.pop(field, None)
            continue
        entity_id = _entity_id(value, domain, field)
        _entity_exists(hass, entity_id, field)

    for field, domain in ENTITY_LIST_FIELDS.items():
        values = result.get(field, [])
        if not isinstance(values, list):
            raise HomeAssistantError(f"{field} must be a list")
        normalized = [_entity_id(value, domain, field) for value in values]
        if len(set(normalized)) != len(normalized):
            raise HomeAssistantError(f"{field} contains duplicate entities")
        for entity_id in normalized:
            _entity_exists(hass, entity_id, field)
        if normalized:
            result[field] = normalized
        else:
            result.pop(field, None)

    phases = result[CONF_PHASE_CURRENT_ENTITIES]
    if len(phases) != 3:
        raise HomeAssistantError("phase_current_entities must contain L1, L2, and L3")

    for field in BOOLEAN_FIELDS & set(result):
        if not isinstance(result[field], bool):
            raise HomeAssistantError(f"{field} must be a boolean")
    for field in STRING_FIELDS & set(result):
        if not isinstance(result[field], str) or not result[field] or len(result[field]) > 64:
            raise HomeAssistantError(f"{field} must be a short non-empty string")
    if (
        CONF_CAPACITY_TARIFF_MODEL in result
        and result[CONF_CAPACITY_TARIFF_MODEL]
        not in {"single_peak", "top3_average", "top5_average"}
    ):
        raise HomeAssistantError("unsupported capacity tariff model")
    for field, (minimum_value, maximum_value) in NUMBER_RANGES.items():
        if field not in result:
            continue
        value = result[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise HomeAssistantError(f"{field} must be numeric")
        if not math.isfinite(value) or not minimum_value <= value <= maximum_value:
            raise HomeAssistantError(
                f"{field} must be between {minimum_value} and {maximum_value}"
            )

    auto_mode = str(result.get(CONF_AUTO_MODE_OPTION, "Auto"))
    charge_mode = str(result.get(CONF_CHARGE_MODE_OPTION, "Charge from Grid"))
    block_discharge_mode = str(
        result.get(CONF_BLOCK_DISCHARGE_MODE_OPTION, "Block Discharging")
    )
    discharge_entity = result.get(CONF_DISCHARGE_POWER_ENTITY)
    discharge_mode = str(result.get(CONF_DISCHARGE_MODE_OPTION, "Discharge to Grid"))
    mode_state = hass.states.get(result[CONF_CONTROL_MODE_ENTITY])
    options = mode_state.attributes.get("options", []) if mode_state else []
    missing_modes = missing_control_modes(
        options,
        auto_mode=auto_mode,
        charge_mode=charge_mode,
        block_discharge_mode=block_discharge_mode,
        discharge_mode=discharge_mode if discharge_entity else None,
    )
    if missing_modes:
        raise HomeAssistantError(
            f"storage control entity is missing modes: {', '.join(missing_modes)}"
        )

    minimum = float(result.get(CONF_MIN_SOC_PCT, 15))
    maximum = float(result.get(CONF_MAX_TARGET_SOC_PCT, 85))
    fuse = float(result.get(CONF_MAIN_FUSE_A, 20))
    margin = float(result.get(CONF_PHASE_SAFETY_MARGIN_A, 1))
    if maximum <= minimum:
        raise HomeAssistantError("maximum target state of charge must exceed minimum")
    if fuse <= 0 or margin < 0 or margin >= fuse:
        raise HomeAssistantError("phase safety margin must be lower than the main fuse")
    return result

