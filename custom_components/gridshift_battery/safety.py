"""Pure safety helpers for live measurement freshness."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime


def sensor_age_seconds(now: datetime, last_updated: datetime | None) -> float:
    """Return a non-negative sensor age, or infinity when no update exists."""
    if last_updated is None:
        return float("inf")
    return max(0.0, (now - last_updated).total_seconds())


def stale_sensor_ids(
    now: datetime,
    sensor_updates: Iterable[tuple[str, datetime | None]],
    max_age_seconds: float,
) -> tuple[str, ...]:
    """Return sensors whose latest state update is older than the safety limit."""
    limit = max(1.0, float(max_age_seconds))
    return tuple(
        entity_id
        for entity_id, last_updated in sensor_updates
        if sensor_age_seconds(now, last_updated) > limit
    )
