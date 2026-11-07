"""Validated configuration bridge between GridShift and Home Assistant."""

from __future__ import annotations

import math
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_AUTO_MODE_OPTION,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_CHARGE_SENSOR,
    CONF_BATTERY_DISCHARGE_SENSOR,
    CONF_BATTERY_POWER_DIRECTION,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BLOCK_DISCHARGE_MODE_OPTION,
    CONF_CAPACITY_TARIFF_ENABLED,
    CONF_CAPACITY_TARIFF_END_HOUR,
    CONF_CAPACITY_TARIFF_MODEL,
    CONF_CAPACITY_TARIFF_RATE_PER_KW,
    CONF_CAPACITY_TARIFF_REFERENCE_KW,
    CONF_CAPACITY_TARIFF_START_HOUR,
    CONF_CHARGE_MODE_OPTION,
    CONF_CHARGE_POWER_ENTITY,
    CONF_CLIMATE_RULES,
    CONF_CONTROL_MODE_ENTITY,
    CONF_CYCLE_COST,
    CONF_DISCHARGE_MODE_OPTION,
    CONF_DISCHARGE_POWER_ENTITY,
    CONF_EV_POWER_ENTITIES,
    CONF_EXPENSIVE_QUANTILE,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_POWER_ENTITY,
    CONF_HEATING_BALANCE_TEMP_C,
    CONF_HEATING_RESERVE_KW_PER_C,
    CONF_HORIZON_HOURS,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_HOUSE_LOAD_DIRECTION,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_HOUSE_LOAD_INCLUDES_EV,
    CONF_HOUSE_LOAD_IS_GRID_POWER,
    CONF_HOUSE_LOAD_SOURCE,
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
    CONF_PRICE_AREA,
    CONF_PRICE_ENTITY,
    CONF_PRICE_MARKUP,
    CONF_PRICE_PROVIDER,
    CONF_PRICE_VAT,
    CONF_ROUND_TRIP_EFFICIENCY,
    CONF_SOC_ENTITY,
    CONF_SOLAR_PEAK_POWER_KWP,
    CONF_SOLAR_POWER_ENTITIES,
    CONF_SWEDISH_WINTER_MODE,
    CONF_THERMAL_INTERVAL,
    CONF_THERMAL_LOSS,
    CONF_THERMAL_MANUAL_HOLD,
    CONF_THERMAL_MASS,
    CONF_THERMAL_MAX_AGE,
    CONF_TIBBER_HOME,
    CONF_WEATHER_ENTITY,
    CONF_WINTER_SOLAR_FACTOR,
)
from .fronius import missing_control_modes
from .power import house_input_is_grid

SERVICE_APPLY_CONFIGURATION = "apply_configuration"
ATTR_CONFIGURATION = "configuration"

ENTITY_FIELDS = {
    CONF_HOUSE_CONSUMPTION_ENTITY: "sensor",
    CONF_GRID_POWER_ENTITY: "sensor",
    CONF_WEATHER_ENTITY: "weather",
    CONF_GRID_EXPORT_SENSOR: "sensor",
    CONF_BATTERY_POWER_ENTITY: "sensor",
    CONF_BATTERY_CHARGE_SENSOR: "sensor",
    CONF_BATTERY_DISCHARGE_SENSOR: "sensor",
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
    CONF_PRICE_PROVIDER, CONF_PRICE_AREA, CONF_PRICE_VAT, CONF_PRICE_MARKUP,
    CONF_TIBBER_HOME, CONF_CLIMATE_RULES, CONF_THERMAL_MASS, CONF_THERMAL_LOSS,
    CONF_THERMAL_MAX_AGE, CONF_THERMAL_INTERVAL, CONF_THERMAL_MANUAL_HOLD,
    CONF_BATTERY_POWER_DIRECTION,
    CONF_HOUSE_LOAD_SOURCE,
    CONF_HOUSE_LOAD_DIRECTION,
    CONF_HOUSE_LOAD_IS_GRID_POWER,
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
    CONF_HOUSE_LOAD_IS_GRID_POWER,
    CONF_CAPACITY_TARIFF_ENABLED,
    CONF_HOUSE_LOAD_INCLUDES_EV,
    CONF_SWEDISH_WINTER_MODE,
}
STRING_FIELDS = {
    CONF_PRICE_PROVIDER, CONF_PRICE_AREA,
    CONF_BATTERY_POWER_DIRECTION,
    CONF_HOUSE_LOAD_SOURCE,
    CONF_HOUSE_LOAD_DIRECTION,
    CONF_AUTO_MODE_OPTION,
    CONF_BLOCK_DISCHARGE_MODE_OPTION,
    CONF_CAPACITY_TARIFF_MODEL,
    CONF_CHARGE_MODE_OPTION,
    CONF_DISCHARGE_MODE_OPTION,
}
NUMBER_RANGES = {
    CONF_PRICE_VAT: (0, 100), CONF_PRICE_MARKUP: (-100, 1000),
    CONF_THERMAL_MASS: (0.5, 200), CONF_THERMAL_LOSS: (0.01, 5),
    CONF_THERMAL_MAX_AGE: (300, 7200), CONF_THERMAL_INTERVAL: (300, 3600),
    CONF_THERMAL_MANUAL_HOLD: (15, 1440),
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
    required = REQUIRED_FIELDS - ({CONF_PRICE_ENTITY} if raw.get(CONF_PRICE_PROVIDER) in {
        "nordpool_direct", "ha_tibber", "ha_nordpool",
    } else set())
    missing = required - set(raw)
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
        if value in (None, "") and field not in required:
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
    measurement_fields = (
        CONF_HOUSE_CONSUMPTION_ENTITY,
        CONF_BATTERY_POWER_ENTITY, CONF_BATTERY_CHARGE_SENSOR,
        CONF_BATTERY_DISCHARGE_SENSOR, CONF_GRID_EXPORT_SENSOR, CONF_GRID_POWER_ENTITY,
    )
    for field in measurement_fields:
        if field not in result:
            continue
        if any(word in result[field] for word in ("gridshift", "recommended", "setpoint")):
            raise HomeAssistantError(f"{field} must be a measurement, not a recommended setpoint")
        state = hass.states.get(result[field])
        if state.attributes.get("unit_of_measurement") not in {"W", "kW", "MW"}:
            raise HomeAssistantError(f"{field} must measure power in W, kW, or MW")
    house_state = hass.states.get(result[CONF_HOUSE_LOAD_ENTITY])
    consumption_id = result.get(CONF_HOUSE_CONSUMPTION_ENTITY)
    if consumption_id:
        consumption = hass.states.get(consumption_id)
        if house_input_is_grid(consumption_id, consumption.attributes.get("friendly_name", "")):
            raise HomeAssistantError("house_consumption_entity must be total house consumption, "
                                     "not a grid import or net meter")
    house_is_grid = house_input_is_grid(
        result[CONF_HOUSE_LOAD_ENTITY], house_state.attributes.get("friendly_name", ""),
        result.get(CONF_HOUSE_LOAD_SOURCE, "auto"),
        result.get(CONF_HOUSE_LOAD_IS_GRID_POWER, False),
    )
    if house_is_grid and not result.get(CONF_HOUSE_CONSUMPTION_ENTITY) and not (
        result.get(CONF_BATTERY_POWER_ENTITY)
        or (result.get(CONF_BATTERY_CHARGE_SENSOR) and result.get(CONF_BATTERY_DISCHARGE_SENSOR))
    ):
        raise HomeAssistantError(
            "Grid power includes battery charging; select a measured battery net power "
            "sensor or both measured charge and discharge sensors to derive house load"
        )
    for field in STRING_FIELDS & set(result):
        if not isinstance(result[field], str) or not result[field] or len(result[field]) > 64:
            raise HomeAssistantError(f"{field} must be a short non-empty string")
    choices = {
        CONF_PRICE_PROVIDER: {"auto", "ha_entity", "ha_tibber", "ha_nordpool", "nordpool_direct"},
        CONF_PRICE_AREA: {"SE1", "SE2", "SE3", "SE4"},
        CONF_BATTERY_POWER_DIRECTION: {"auto", "positive_charging", "positive_discharging"},
        CONF_HOUSE_LOAD_DIRECTION: {"auto", "positive_consumption", "negative_consumption"},
        CONF_HOUSE_LOAD_SOURCE: {"auto", "house", "grid"},
    }
    for field, options in choices.items():
        if field in result and result[field] not in options:
            raise HomeAssistantError(f"unsupported {field}")
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
    home = result.get(CONF_TIBBER_HOME, "")
    if not isinstance(home, str) or len(home) > 128:
        raise HomeAssistantError("tibber_home must be a home name or an empty string")
    if CONF_CLIMATE_RULES in result:
        result[CONF_CLIMATE_RULES] = validate_climate_rules(hass, result[CONF_CLIMATE_RULES])
    return result


def validate_climate_rules(hass, rules) -> list[dict]:
    """Validate each selected zone independently before any physical write is possible."""
    if not isinstance(rules, list) or len(rules) > 40:
        raise HomeAssistantError("climate_rules must be a list of at most 40 zones")
    units = getattr(getattr(hass, "config", None), "units", None)
    if rules and getattr(units, "temperature_unit", "°C") != "°C":
        raise HomeAssistantError("Thermostat control currently requires Home Assistant units °C")
    normalized, seen = [], set()
    allowed = {"entity_id", "baseline", "max_deviation", "weight", "mode", "direction",
               "heater_type", "scop", "max_power_w", "phase"}
    for raw in rules:
        if not isinstance(raw, dict) or set(raw) != allowed:
            raise HomeAssistantError("Each climate rule needs a complete zone configuration")
        rule = dict(raw)
        entity_id = _entity_id(rule["entity_id"], "climate", "climate_rules")
        _entity_exists(hass, entity_id, "climate_rules")
        if entity_id in seen:
            raise HomeAssistantError("climate_rules contains duplicate zones")
        seen.add(entity_id)
        for key, lower, upper in (
            ("baseline", 14, 30), ("max_deviation", 0.2, 4), ("weight", 1, 10),
            ("scop", 1, 7), ("max_power_w", 50, 50000),
        ):
            value = rule[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not (
                math.isfinite(value) and lower <= value <= upper
            ):
                raise HomeAssistantError(f"Invalid climate {key} for {entity_id}")
        options = {
            "mode": {"scale", "peak"}, "direction": {"auto", "heat", "cool", "heat_cool"},
            "phase": {"unknown", "L1", "L2", "L3", "3_phase"},
            "heater_type": {"air_air", "air_water", "ground_source", "direct_electric"},
        }
        if any(rule[key] not in values for key, values in options.items()):
            raise HomeAssistantError(f"Invalid climate strategy or equipment for {entity_id}")
        normalized.append(rule)
    return normalized

