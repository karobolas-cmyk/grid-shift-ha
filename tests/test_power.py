"""Power polarity and measurement roles must agree with the dashboard."""

from custom_components.gridshift_battery.power import (
    battery_power_inverted,
    grid_power_is_net,
    house_from_grid_w,
    house_input_is_grid,
    house_power_inverted,
    observed_grid_charge_w,
)


def test_fronius_net_battery_is_negative_during_charging() -> None:
    assert battery_power_inverted("sensor.solarnet_power_battery")
    assert battery_power_inverted("sensor.fronius_battery_power")
    assert not battery_power_inverted("sensor.generic_battery_power")
    battery_w = -2400 * (-1 if battery_power_inverted("sensor.solarnet_power_battery") else 1)
    assert battery_w == 2400
    assert house_from_grid_w(10400, 100, battery_w, 0) == 8100


def test_manual_polarity_has_priority_over_vendor_detection() -> None:
    assert not battery_power_inverted(
        "sensor.solarnet_power_battery", direction="positive_charging"
    )
    assert battery_power_inverted("sensor.generic", direction="positive_discharging")
    assert house_power_inverted("sensor.generic", "negative_consumption")
    assert not house_power_inverted("sensor.solarnet_power_load", "positive_consumption")


def test_raw_fronius_load_and_consumed_sensor_have_different_signs() -> None:
    assert house_power_inverted("sensor.solarnet_power_load")
    assert not house_power_inverted("sensor.solarnet_power_load_consumed")


def test_tibber_house_field_is_grid_power_unless_overridden() -> None:
    assert house_input_is_grid("sensor.power_otto", "Tibber Pulse otto Effekt")
    assert house_input_is_grid("sensor.custom", source="grid")
    assert not house_input_is_grid("sensor.power_otto", "Tibber Pulse otto Effekt", "house")
    assert house_from_grid_w(12273, 100, 10000, 0) == 2373


def test_signed_grid_power_already_contains_export() -> None:
    assert grid_power_is_net("sensor.solarnet_power_grid")
    assert not grid_power_is_net("sensor.solarnet_power_grid_import")
    assert house_from_grid_w(-5000, 7300, 0, 0) == 2300


def test_phase_budget_does_not_treat_an_undelivered_setpoint_as_load() -> None:
    assert observed_grid_charge_w(10000, 2400, 100) == 2300
    assert observed_grid_charge_w(10000, 2400, 10000) == 0
    assert observed_grid_charge_w(2000, 2400, 100) == 2000
    assert observed_grid_charge_w(10000, -2400, 100) == 0
