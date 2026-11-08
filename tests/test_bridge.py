from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.gridshift_battery.bridge import (
    validate_climate_rules,
    validate_configuration,
)
from custom_components.gridshift_battery.const import (
    CONF_BATTERY_CHARGE_SENSOR,
    CONF_BATTERY_DISCHARGE_SENSOR,
    CONF_BATTERY_POWER_ENTITY,
    CONF_CHARGE_POWER_ENTITY,
    CONF_CONTROL_MODE_ENTITY,
    CONF_DISCHARGE_POWER_ENTITY,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_HOUSE_LOAD_IS_GRID_POWER,
    CONF_PHASE_CURRENT_ENTITIES,
    CONF_PRICE_ENTITY,
    CONF_SOC_ENTITY,
)


class States:
    def __init__(self) -> None:
        self.values = {
            "sensor.price": SimpleNamespace(state="1.2", attributes={}),
            "sensor.soc": SimpleNamespace(state="50", attributes={}),
            "sensor.house": SimpleNamespace(state="2000", attributes={}),
            "sensor.l1": SimpleNamespace(state="5", attributes={}),
            "sensor.l2": SimpleNamespace(state="6", attributes={}),
            "sensor.l3": SimpleNamespace(state="7", attributes={}),
            "select.mode": SimpleNamespace(
                state="Auto",
                attributes={
                    "options": [
                        "Auto",
                        "Charge from Grid",
                        "Block Discharging",
                        "Discharge to Grid",
                    ]
                },
            ),
            "number.charge": SimpleNamespace(state="unavailable", attributes={}),
        }

    def get(self, entity_id: str):
        return self.values.get(entity_id)


def valid_configuration() -> dict:
    return {
        CONF_PRICE_ENTITY: "sensor.price",
        CONF_SOC_ENTITY: "sensor.soc",
        CONF_HOUSE_LOAD_ENTITY: "sensor.house",
        CONF_PHASE_CURRENT_ENTITIES: ["sensor.l1", "sensor.l2", "sensor.l3"],
        CONF_CONTROL_MODE_ENTITY: "select.mode",
        CONF_CHARGE_POWER_ENTITY: "number.charge",
    }


def test_bridge_accepts_unavailable_mode_specific_number() -> None:
    hass = SimpleNamespace(states=States())
    assert validate_configuration(hass, valid_configuration()) == valid_configuration()


def test_bridge_rejects_wrong_entity_domain() -> None:
    hass = SimpleNamespace(states=States())
    configuration = valid_configuration()
    configuration[CONF_CHARGE_POWER_ENTITY] = "sensor.house"
    with pytest.raises(HomeAssistantError, match="must be a number entity"):
        validate_configuration(hass, configuration)


def test_bridge_requires_three_unique_phases() -> None:
    hass = SimpleNamespace(states=States())
    configuration = valid_configuration()
    configuration[CONF_PHASE_CURRENT_ENTITIES] = ["sensor.l1", "sensor.l1", "sensor.l3"]
    with pytest.raises(HomeAssistantError, match="duplicate"):
        validate_configuration(hass, configuration)


def test_bridge_rejects_unknown_fields() -> None:
    hass = SimpleNamespace(states=States())
    configuration = {**valid_configuration(), "call_any_service": True}
    with pytest.raises(HomeAssistantError, match="unsupported configuration fields"):
        validate_configuration(hass, configuration)


def test_bridge_rejects_discharge_entity_mapped_as_charge() -> None:
    hass = SimpleNamespace(states=States())
    configuration = valid_configuration()
    configuration[CONF_CHARGE_POWER_ENTITY] = "number.byd_grid_discharge_power_2"
    with pytest.raises(HomeAssistantError, match="distinct"):
        validate_configuration(hass, configuration)


def test_bridge_rejects_same_entity_for_both_power_roles() -> None:
    hass = SimpleNamespace(states=States())
    configuration = valid_configuration()
    configuration[CONF_DISCHARGE_POWER_ENTITY] = configuration[CONF_CHARGE_POWER_ENTITY]
    with pytest.raises(HomeAssistantError, match="distinct"):
        validate_configuration(hass, configuration)


def test_grid_load_requires_measured_battery_power() -> None:
    hass = SimpleNamespace(states=States())
    configuration = {**valid_configuration(), CONF_HOUSE_LOAD_IS_GRID_POWER: True}
    with pytest.raises(HomeAssistantError, match="Grid power includes battery charging"):
        validate_configuration(hass, configuration)


def test_grid_load_accepts_measured_charge_and_discharge_pair() -> None:
    hass = SimpleNamespace(states=States())
    hass.states.values["sensor.battery_charge"] = SimpleNamespace(
        state="10000", attributes={"unit_of_measurement": "W"},
    )
    hass.states.values["sensor.battery_discharge"] = SimpleNamespace(
        state="0", attributes={"unit_of_measurement": "W"},
    )
    configuration = {
        **valid_configuration(), CONF_HOUSE_LOAD_IS_GRID_POWER: True,
        CONF_BATTERY_CHARGE_SENSOR: "sensor.battery_charge",
        CONF_BATTERY_DISCHARGE_SENSOR: "sensor.battery_discharge",
    }
    assert validate_configuration(hass, configuration) == configuration


def test_grid_load_rejects_battery_percentage_as_power() -> None:
    hass = SimpleNamespace(states=States())
    hass.states.values["sensor.battery_power"] = SimpleNamespace(
        state="100", attributes={"unit_of_measurement": "%"},
    )
    configuration = {
        **valid_configuration(), CONF_HOUSE_LOAD_IS_GRID_POWER: True,
        CONF_BATTERY_POWER_ENTITY: "sensor.battery_power",
    }
    with pytest.raises(HomeAssistantError, match="must measure power"):
        validate_configuration(hass, configuration)


def test_grid_load_rejects_our_own_recommendation_as_a_measurement() -> None:
    hass = SimpleNamespace(states=States())
    entity_id = "sensor.gridshift_recommended_charge_power"
    hass.states.values[entity_id] = SimpleNamespace(
        state="10000", attributes={"unit_of_measurement": "W"},
    )
    configuration = {**valid_configuration(), CONF_BATTERY_POWER_ENTITY: entity_id}
    with pytest.raises(HomeAssistantError, match="must be a measurement"):
        validate_configuration(hass, configuration)


def test_bridge_accepts_manual_polarity_and_source_overrides() -> None:
    hass = SimpleNamespace(states=States())
    configuration = {
        **valid_configuration(), "battery_power_direction": "positive_discharging",
        "house_load_direction": "negative_consumption", "house_load_source": "house",
    }
    assert validate_configuration(hass, configuration) == configuration


def test_bridge_rejects_unsupported_polarity() -> None:
    hass = SimpleNamespace(states=States())
    configuration = {**valid_configuration(), "battery_power_direction": "guess"}
    with pytest.raises(HomeAssistantError, match="unsupported battery_power_direction"):
        validate_configuration(hass, configuration)


def test_direct_price_provider_does_not_need_an_ha_price_entity() -> None:
    config = {**valid_configuration(), "price_provider": "nordpool_direct", "price_area": "SE3"}
    del config[CONF_PRICE_ENTITY]
    assert validate_configuration(SimpleNamespace(states=States()), config) == config


def test_thermostat_configuration_rejects_unbounded_or_unknown_targets() -> None:
    states = States()
    states.values["climate.room"] = SimpleNamespace(state="heat", attributes={})
    hass = SimpleNamespace(states=states)
    rule = {"entity_id": "climate.room", "baseline": 21, "max_deviation": 1, "weight": 6,
            "mode": "scale", "direction": "auto", "heater_type": "air_air", "scop": 3.5,
            "max_power_w": 1500, "phase": "unknown"}
    assert validate_climate_rules(hass, [rule]) == [rule]
    for policy in ("both", "setback_only", "lower_only"):
        configured = {**rule, "adjustment": policy}
        assert validate_climate_rules(hass, [configured]) == [configured]
    with pytest.raises(HomeAssistantError, match="temperature adjustment"):
        validate_climate_rules(hass, [{**rule, "adjustment": "arbitrary"}])
    with pytest.raises(HomeAssistantError, match="requires heating"):
        validate_climate_rules(hass, [{**rule, "adjustment": "lower_only", "direction": "cool"}])
    with pytest.raises(HomeAssistantError, match="Invalid climate max_deviation"):
        validate_climate_rules(hass, [{**rule, "max_deviation": 10}])
    with pytest.raises(HomeAssistantError, match="complete zone configuration"):
        validate_climate_rules(hass, [{**rule, "extra_service": "turn_on"}])
    with pytest.raises(HomeAssistantError, match="must be a climate entity"):
        validate_climate_rules(hass, [{**rule, "entity_id": "light.room"}])


@pytest.mark.parametrize("baseline", [10, 37, 40])
def test_spa_baseline_uses_the_selected_devices_temperature_limits(baseline):
    states = States()
    states.values["climate.spa"] = SimpleNamespace(
        state="heat", attributes={"min_temp": 10, "max_temp": 40},
    )
    rule = {"entity_id": "climate.spa", "baseline": baseline, "max_deviation": 1,
            "weight": 6, "mode": "scale", "direction": "auto", "heater_type": "direct_electric",
            "scop": 1, "max_power_w": 3000, "phase": "L1"}
    hass = SimpleNamespace(states=states)
    assert validate_climate_rules(hass, [rule]) == [rule]
    for invalid in (9, 41):
        with pytest.raises(HomeAssistantError, match=r"baseline.*climate.spa.*10.*40"):
            validate_climate_rules(hass, [{**rule, "baseline": invalid}])


@pytest.mark.parametrize("attributes,baseline,valid", [
    ({"min_temp": 7, "max_temp": 30}, 37, False),
    ({}, 37, False),
    ({}, 21, True),
    ({"min_temp": 0, "max_temp": 40}, 0, True),
])
def test_climate_baselines_keep_individual_limits_and_missing_attribute_defaults(
    attributes, baseline, valid,
):
    states = States()
    states.values["climate.zone"] = SimpleNamespace(state="heat", attributes=attributes)
    rule = {"entity_id": "climate.zone", "baseline": baseline, "max_deviation": 1,
            "weight": 6, "mode": "scale", "direction": "auto", "heater_type": "air_air",
            "scop": 3, "max_power_w": 1000, "phase": "unknown"}
    if valid:
        assert validate_climate_rules(SimpleNamespace(states=states), [rule]) == [rule]
    else:
        with pytest.raises(HomeAssistantError, match="Invalid climate baseline"):
            validate_climate_rules(SimpleNamespace(states=states), [rule])


@pytest.mark.parametrize("attributes", [
    {"min_temp": 40, "max_temp": 10}, {"min_temp": True, "max_temp": 40},
    {"min_temp": 10, "max_temp": float("inf")},
    {"min_temp": 10, "max_temp": float("nan")},
])
def test_climate_configuration_rejects_invalid_device_temperature_limits(attributes):
    states = States()
    states.values["climate.spa"] = SimpleNamespace(state="heat", attributes=attributes)
    rule = {"entity_id": "climate.spa", "baseline": 37, "max_deviation": 1,
            "weight": 6, "mode": "scale", "direction": "auto", "heater_type": "direct_electric",
            "scop": 1, "max_power_w": 3000, "phase": "L1"}
    with pytest.raises(HomeAssistantError, match="Invalid climate temperature limits"):
        validate_climate_rules(SimpleNamespace(states=states), [rule])


def test_total_consumption_is_separate_from_grid_and_does_not_require_battery_measurements():
    states = States()
    states.values["sensor.solarnet_power_load_consumed"] = SimpleNamespace(
        state="4210", attributes={"unit_of_measurement": "W"},
    )
    states.values["sensor.solarnet_elnat_import"] = SimpleNamespace(
        state="288", attributes={"unit_of_measurement": "W"},
    )
    config = {**valid_configuration(), "house_load_entity": "sensor.solarnet_elnat_import",
              "house_load_source": "grid", "grid_power_entity": "sensor.solarnet_elnat_import",
              "house_consumption_entity": "sensor.solarnet_power_load_consumed"}
    assert validate_configuration(SimpleNamespace(states=states), config) == config
    with pytest.raises(HomeAssistantError, match="not a grid import"):
        validate_configuration(SimpleNamespace(states=states), {
            **config, "house_consumption_entity": "sensor.solarnet_elnat_import",
        })

