"""Adapters for common Home Assistant electricity-price entity attributes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

from .models import PriceSlot

_START_KEYS = ("start", "start_time", "datetime", "time", "from")
_END_KEYS = ("end", "end_time", "to")
_PRICE_KEYS = ("price", "value", "total", "energy_price")
_SERIES_KEYS = ("prices", "raw_today", "raw_tomorrow", "today", "tomorrow")


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _datetime(value: Any, timezone: tzinfo, day: date | None = None) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, time) and day:
        parsed = datetime.combine(day, value)
    elif isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            if day is None:
                return None
            try:
                parsed = datetime.combine(day, time.fromisoformat(text))
            except ValueError:
                return None
    else:
        return None
    return parsed.replace(tzinfo=timezone) if parsed.tzinfo is None else parsed.astimezone(timezone)


def _mapping_value(item: Mapping[str, Any], keys: Iterable[str]) -> Any:
    return next((item[key] for key in keys if key in item), None)


def _parse_mapping_series(
    values: list[Any], timezone: tzinfo, day: date | None
) -> list[PriceSlot]:
    pending: list[tuple[datetime, datetime | None, float]] = []
    for item in values:
        if not isinstance(item, Mapping):
            continue
        start = _datetime(_mapping_value(item, _START_KEYS), timezone, day)
        price = _number(_mapping_value(item, _PRICE_KEYS))
        end = _datetime(_mapping_value(item, _END_KEYS), timezone, day)
        if start is not None and price is not None:
            pending.append((start, end, price))

    pending.sort(key=lambda value: value[0])
    slots: list[PriceSlot] = []
    inferred_step = (
        pending[-1][0] - pending[-2][0]
        if len(pending) > 1
        else timedelta(hours=1)
    )
    for index, (start, end, price) in enumerate(pending):
        if end is None:
            end = (
                pending[index + 1][0]
                if index + 1 < len(pending)
                else start + inferred_step
            )
        if end > start:
            slots.append(PriceSlot(start=start, end=end, price=price))
    return slots


def _parse_numeric_series(
    values: list[Any], timezone: tzinfo, day: date | None
) -> list[PriceSlot]:
    if day is None:
        return []
    numbers = [_number(value) for value in values]
    if not numbers or any(value is None for value in numbers):
        return []
    step = timedelta(days=1) / len(numbers)
    midnight = datetime.combine(day, time.min, timezone)
    return [
        PriceSlot(midnight + index * step, midnight + (index + 1) * step, float(value))
        for index, value in enumerate(numbers)
    ]


def parse_price_slots(
    attributes: Mapping[str, Any], now: datetime, current_price: Any = None
) -> tuple[PriceSlot, ...]:
    """Normalize common price attributes into ordered, de-duplicated slots.

    Supported forms include Nord Pool-style ``raw_today``/``raw_tomorrow``, a
    generic ``prices`` list containing mappings, and numeric hourly/quarter-hourly
    ``today``/``tomorrow`` lists.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    parsed: list[PriceSlot] = []
    for key in _SERIES_KEYS:
        values = attributes.get(key)
        if not isinstance(values, list) or not values:
            continue
        if key == "today" or key == "raw_today":
            day = now.date()
        elif key == "tomorrow" or key == "raw_tomorrow":
            day = now.date() + timedelta(days=1)
        else:
            day = None
        if isinstance(values[0], Mapping):
            parsed.extend(_parse_mapping_series(values, now.tzinfo, day))
        else:
            parsed.extend(_parse_numeric_series(values, now.tzinfo, day))

    unique: dict[tuple[datetime, datetime], PriceSlot] = {
        (slot.start, slot.end): slot for slot in parsed
    }
    slots = sorted(unique.values(), key=lambda slot: slot.start)

    # A state-only price remains useful for diagnostics, but cannot support a
    # future plan. Give it a short validity window so the decision fails safe.
    if not slots and (price := _number(current_price)) is not None:
        floor = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
        slots = [PriceSlot(floor, floor + timedelta(minutes=15), price)]
    return tuple(slots)


def current_slot(slots: tuple[PriceSlot, ...], now: datetime) -> PriceSlot | None:
    """Return the price slot active at ``now``."""
    return next((slot for slot in slots if slot.start <= now < slot.end), None)
