"""Tests for electricity-price entity adapters."""

from datetime import datetime
from zoneinfo import ZoneInfo

from custom_components.gridshift_battery.price import parse_price_slots

TZ = ZoneInfo("Europe/Stockholm")
NOW = datetime(2026, 11, 6, 10, 15, tzinfo=TZ)


def test_parses_nordpool_raw_lists() -> None:
    slots = parse_price_slots(
        {
            "raw_today": [
                {
                    "start": "2026-11-06T10:00:00+01:00",
                    "end": "2026-11-06T11:00:00+01:00",
                    "value": 0.42,
                },
                {
                    "start": "2026-11-06T11:00:00+01:00",
                    "end": "2026-11-06T12:00:00+01:00",
                    "value": 0.61,
                },
            ],
            "raw_tomorrow": [
                {
                    "start": "2026-11-07T00:00:00+01:00",
                    "end": "2026-11-07T01:00:00+01:00",
                    "value": 0.25,
                }
            ],
        },
        NOW,
    )
    assert [slot.price for slot in slots] == [0.42, 0.61, 0.25]
    assert all(slot.start.tzinfo for slot in slots)


def test_parses_numeric_quarter_hour_prices() -> None:
    slots = parse_price_slots({"today": [0.1] * 96}, NOW)
    assert len(slots) == 96
    assert slots[0].duration_hours == 0.25


def test_state_only_price_is_not_a_fake_forecast() -> None:
    slots = parse_price_slots({}, NOW, "0.55")
    assert len(slots) == 1
    assert slots[0].price == 0.55


def test_tibber_quarter_prices_keep_fifteen_minute_resolution() -> None:
    slots = parse_price_slots(
        {
            "prices": [
                {"start_time": "2026-11-06T10:00:00+01:00", "price": 0.41},
                {"start_time": "2026-11-06T10:15:00+01:00", "price": 0.38},
                {"start_time": "2026-11-06T10:30:00+01:00", "price": 0.44},
            ]
        },
        NOW,
    )
    assert len(slots) == 3
    assert all(slot.duration_hours == 0.25 for slot in slots)
