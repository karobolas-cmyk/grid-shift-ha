from custom_components.gridshift_battery.const import (
    CONF_CHARGE_POWER_ENTITY,
    CONF_CONTROL_MODE_ENTITY,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_PHASE_CURRENT_ENTITIES,
    CONF_PRICE_ENTITY,
    CONF_SOC_ENTITY,
    is_configured,
)


def test_empty_bridge_is_not_runnable() -> None:
    assert not is_configured({})


def test_complete_mapping_is_runnable() -> None:
    assert is_configured(
        {
            CONF_PRICE_ENTITY: "sensor.price",
            CONF_SOC_ENTITY: "sensor.battery_soc",
            CONF_HOUSE_LOAD_ENTITY: "sensor.house_load",
            CONF_PHASE_CURRENT_ENTITIES: [
                "sensor.current_l1",
                "sensor.current_l2",
                "sensor.current_l3",
            ],
            CONF_CONTROL_MODE_ENTITY: "select.storage_control_mode",
            CONF_CHARGE_POWER_ENTITY: "number.grid_charge_power",
        }
    )
