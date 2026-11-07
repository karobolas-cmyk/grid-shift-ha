"""A repeated zero report must not be confused with a stopped integration."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from custom_components.gridshift_battery.measurements import power_snapshot, report_time


def test_unchanged_zero_keeps_its_actual_report_time():
    now = datetime(2026, 11, 7, 16, tzinfo=UTC)
    state = SimpleNamespace(
        state="0",
        last_reported=now,
        last_updated=now - timedelta(hours=3),
        attributes={"unit_of_measurement": "W"},
    )
    hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: state))
    snapshot = power_snapshot(hass, {"battery_charge_power_sensor": "sensor.charge"}, now)
    assert snapshot["entities"]["sensor.charge"]["state"] == "0"
    assert snapshot["entities"]["sensor.charge"]["last_reported"] == now.isoformat()
    assert report_time(state) == now


def test_polling_does_not_refresh_a_stale_sensor_timestamp():
    now = datetime(2026, 11, 7, 16, tzinfo=UTC)
    old = now - timedelta(hours=3)
    state = SimpleNamespace(
        state="0", last_reported=old, last_updated=old, attributes={"unit_of_measurement": "W"}
    )
    hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: state))
    snapshot = power_snapshot(hass, {"grid_power_entity": "sensor.grid"}, now)
    assert snapshot["captured_at"] == now.isoformat()
    assert snapshot["entities"]["sensor.grid"]["last_reported"] == old.isoformat()
