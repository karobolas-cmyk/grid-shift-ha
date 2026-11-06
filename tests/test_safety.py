from datetime import UTC, datetime, timedelta

from custom_components.gridshift_battery.safety import (
    sensor_age_seconds,
    stale_sensor_ids,
)


def test_phase_sensor_age_is_non_negative() -> None:
    now = datetime(2026, 11, 6, 20, 0, tzinfo=UTC)
    assert sensor_age_seconds(now, now - timedelta(seconds=31)) == 31
    assert sensor_age_seconds(now, now + timedelta(seconds=1)) == 0


def test_stale_phase_sensor_is_identified() -> None:
    now = datetime(2026, 11, 6, 20, 0, tzinfo=UTC)
    updates = (
        ("sensor.l1", now - timedelta(seconds=15)),
        ("sensor.l2", now - timedelta(seconds=121)),
        ("sensor.l3", now - timedelta(seconds=30)),
    )
    assert stale_sensor_ids(now, updates, 120) == ("sensor.l2",)
