"""Constants for GridShift Battery Optimizer."""

from __future__ import annotations

DOMAIN = "gridshift_battery"

CONF_PRICE_ENTITY = "price_entity"
CONF_SOC_ENTITY = "soc_entity"
CONF_HOUSE_LOAD_ENTITY = "house_load_entity"
CONF_EV_POWER_ENTITY = "ev_power_entity"
CONF_EV_POWER_ENTITIES = "ev_power_entities"
CONF_HOUSE_LOAD_INCLUDES_EV = "house_load_includes_ev"
CONF_SOLAR_POWER_ENTITY = "solar_power_entity"
CONF_SOLAR_POWER_ENTITIES = "solar_power_entities"
CONF_OUTDOOR_TEMPERATURE_ENTITY = "outdoor_temperature_entity"
CONF_PHASE_CURRENT_ENTITIES = "phase_current_entities"
CONF_CONTROL_MODE_ENTITY = "control_mode_entity"
CONF_CHARGE_POWER_ENTITY = "charge_power_entity"
CONF_DISCHARGE_POWER_ENTITY = "discharge_power_entity"
CONF_PRICE_PROVIDER = "price_provider"
CONF_METER_PROVIDER = "meter_provider"
CONF_SOLAR_PROVIDER = "solar_provider"
CONF_EV_PROVIDER = "ev_provider"

CONF_BATTERY_CAPACITY_KWH = "battery_capacity_kwh"
CONF_MIN_SOC_PCT = "min_soc_pct"
CONF_MAX_TARGET_SOC_PCT = "max_target_soc_pct"
CONF_MAX_CHARGE_POWER_W = "max_charge_power_w"
CONF_MAX_DISCHARGE_POWER_W = "max_discharge_power_w"
CONF_MAIN_FUSE_A = "main_fuse_a"
CONF_PHASE_SAFETY_MARGIN_A = "phase_safety_margin_a"
CONF_PHASE_SENSOR_MAX_AGE_SECONDS = "phase_sensor_max_age_seconds"
CONF_NOMINAL_VOLTAGE_V = "nominal_voltage_v"
CONF_INTERVAL_MINUTES = "interval_minutes"
CONF_HORIZON_HOURS = "horizon_hours"
CONF_EXPENSIVE_QUANTILE = "expensive_quantile"
CONF_MIN_SAVINGS_PERCENT = "min_savings_percent"
CONF_ROUND_TRIP_EFFICIENCY = "round_trip_efficiency"
CONF_CYCLE_COST = "cycle_cost"
CONF_CHARGE_MODE_OPTION = "charge_mode_option"
CONF_DISCHARGE_MODE_OPTION = "discharge_mode_option"
CONF_AUTO_MODE_OPTION = "auto_mode_option"
CONF_MODE_SETTLE_SECONDS = "mode_settle_seconds"
CONF_SWEDISH_WINTER_MODE = "swedish_winter_mode"
CONF_WINTER_SOLAR_FACTOR = "winter_solar_factor"
CONF_HEATING_BALANCE_TEMP_C = "heating_balance_temp_c"
CONF_HEATING_RESERVE_KW_PER_C = "heating_reserve_kw_per_c"
CONF_CAPACITY_TARIFF_ENABLED = "capacity_tariff_enabled"
CONF_CAPACITY_TARIFF_MODEL = "capacity_tariff_model"
CONF_CAPACITY_TARIFF_RATE_PER_KW = "capacity_tariff_rate_per_kw"
CONF_CAPACITY_TARIFF_REFERENCE_KW = "capacity_tariff_reference_kw"
CONF_CAPACITY_TARIFF_START_HOUR = "capacity_tariff_start_hour"
CONF_CAPACITY_TARIFF_END_HOUR = "capacity_tariff_end_hour"

DEFAULTS = {
    CONF_BATTERY_CAPACITY_KWH: 10.0,
    CONF_MIN_SOC_PCT: 15.0,
    CONF_MAX_TARGET_SOC_PCT: 85.0,
    CONF_MAX_CHARGE_POWER_W: 5000,
    CONF_MAX_DISCHARGE_POWER_W: 5000,
    CONF_MAIN_FUSE_A: 20.0,
    CONF_PHASE_SAFETY_MARGIN_A: 1.0,
    CONF_PHASE_SENSOR_MAX_AGE_SECONDS: 120,
    CONF_NOMINAL_VOLTAGE_V: 230.0,
    CONF_INTERVAL_MINUTES: 5,
    CONF_HORIZON_HOURS: 24,
    CONF_EXPENSIVE_QUANTILE: 0.65,
    CONF_MIN_SAVINGS_PERCENT: 8.0,
    CONF_ROUND_TRIP_EFFICIENCY: 0.88,
    CONF_CYCLE_COST: 0.0,
    CONF_HOUSE_LOAD_INCLUDES_EV: True,
    CONF_CHARGE_MODE_OPTION: "Charge from Grid",
    CONF_DISCHARGE_MODE_OPTION: "Discharge to Grid",
    CONF_AUTO_MODE_OPTION: "Auto",
    CONF_MODE_SETTLE_SECONDS: 20,
    CONF_SWEDISH_WINTER_MODE: True,
    CONF_WINTER_SOLAR_FACTOR: 0.20,
    CONF_HEATING_BALANCE_TEMP_C: 8.0,
    CONF_HEATING_RESERVE_KW_PER_C: 0.07,
    CONF_CAPACITY_TARIFF_ENABLED: False,
    CONF_CAPACITY_TARIFF_MODEL: "top3_average",
    CONF_CAPACITY_TARIFF_RATE_PER_KW: 95.0,
    CONF_CAPACITY_TARIFF_REFERENCE_KW: 8.0,
    CONF_CAPACITY_TARIFF_START_HOUR: 6,
    CONF_CAPACITY_TARIFF_END_HOUR: 22,
}

FRONIUS_DOMAIN = "fronius_modbus"
STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = f"{DOMAIN}."

PLATFORMS = ["sensor", "switch"]

REQUIRED_CONFIGURATION_KEYS = frozenset(
    {
        CONF_PRICE_ENTITY,
        CONF_SOC_ENTITY,
        CONF_HOUSE_LOAD_ENTITY,
        CONF_PHASE_CURRENT_ENTITIES,
        CONF_CONTROL_MODE_ENTITY,
        CONF_CHARGE_POWER_ENTITY,
    }
)


def is_configured(data: dict) -> bool:
    """Return whether an entry contains the minimum runnable mapping."""
    return REQUIRED_CONFIGURATION_KEYS.issubset(data)
