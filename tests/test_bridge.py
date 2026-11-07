from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.gridshift_battery.bridge import validate_configuration
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

