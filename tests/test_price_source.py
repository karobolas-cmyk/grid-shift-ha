"""Price ownership, published coverage, caching, and explicit source selection."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from custom_components.gridshift_battery.const import DEFAULTS
from custom_components.gridshift_battery.models import PriceSlot
from custom_components.gridshift_battery.price import parse_price_slots
from custom_components.gridshift_battery.price_source import PriceSource, contiguous_prices

NOW = datetime(2026, 11, 7, 15, 45, tzinfo=UTC)


class Services:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result or {}, error, []

    def has_service(self, domain, service):
        return True

    async def async_call(self, domain, service, data, **kwargs):
        self.calls.append((domain, service, data))
        if self.error:
            raise self.error
        return self.result


def source(provider="nordpool_direct", attributes=None, services=None, **config):
    state = SimpleNamespace(state="3.10", attributes=attributes or {})
    hass = SimpleNamespace(
        states=SimpleNamespace(get=lambda _: state),
        services=services or Services(),
        config_entries=SimpleNamespace(async_entries=lambda _: []),
    )
    storage = SimpleNamespace(state=SimpleNamespace(price_cache={}))
    return PriceSource(
        hass,
        {**DEFAULTS, "price_provider": provider, "price_entity": "sensor.price", **config},
        storage,
    )


def nordpool_response():
    return {
        "multiAreaEntries": [
            {
                "deliveryStart": (NOW + timedelta(minutes=15 * i)).isoformat(),
                "deliveryEnd": (NOW + timedelta(minutes=15 * (i + 1))).isoformat(),
                "entryPerArea": {"SE3": value, "SE4": 9000},
            }
            for i, value in enumerate((1000, 2000, 1500, 3000))
        ]
    }


def test_direct_prices_use_selected_area_units_vat_markup_and_exact_published_end():
    item = source(price_vat_percent=25, price_markup_ore_kwh=10)
    calls = []

    async def fetch(params):
        calls.append(params)
        return nordpool_response() if params["date"] == NOW.date().isoformat() else {}

    item.fetch_json = fetch
    rows = asyncio.run(item.async_prices(NOW))
    assert abs(rows[0].price - 1.375) < 1e-9
    assert len(rows) == 4 and rows[-1].end == NOW + timedelta(hours=1)
    assert calls[0]["deliveryArea"] == "SE3"
    assert len(item.snapshot["slots"]) == 4
    assert item.snapshot["coverage_end"] == rows[-1].end.isoformat()


def test_published_cache_survives_transport_failure_but_expires():
    item = source()

    async def fetch(params):
        return nordpool_response()

    item.fetch_json = fetch
    asyncio.run(item.async_prices(NOW))

    async def fail(params):
        raise ConnectionError("offline")

    item.fetch_json = fail
    assert len(asyncio.run(item.async_prices(NOW + timedelta(minutes=6)))) == 4
    assert item.snapshot["status"] == "ready_cached"
    assert asyncio.run(item.async_prices(NOW + timedelta(hours=1))) == ()
    assert item.snapshot["status"] == "waiting"


def test_generic_entity_never_silently_borrows_tibber_prices():
    item = source("auto", services=Services({"prices": {"Other house": []}}))
    assert asyncio.run(item.async_prices(NOW)) == ()
    assert item.hass.services.calls == []
    assert item.snapshot["effective_provider"] == "ha_entity"


def test_tibber_requires_a_single_home_or_an_explicit_home_selection():
    rows = [
        {"start_time": row["deliveryStart"], "price": 3.1}
        for row in nordpool_response()["multiAreaEntries"]
    ]
    services = Services({"prices": {"House A": rows, "House B": rows}})
    ambiguous = source("ha_tibber", services=services)
    assert asyncio.run(ambiguous.async_prices(NOW)) == ()
    assert ambiguous.snapshot["error"] == "tibber_home_selection_required"
    selected = source("ha_tibber", services=services, tibber_home="House B")
    assert asyncio.run(selected.async_prices(NOW))[0].price == 3.1
    assert selected.snapshot["tibber_home"] == "House B"


def test_explicit_nordpool_does_not_use_a_different_entity_integration():
    attributes = {
        "prices": [
            {"start": row["deliveryStart"], "end": row["deliveryEnd"], "price": 1}
            for row in nordpool_response()["multiAreaEntries"]
        ]
    }
    item = source("ha_nordpool", attributes=attributes)
    assert asyncio.run(item.async_prices(NOW)) == ()
    assert item.snapshot["error"] == "nordpool_entry_selection_required"


def test_price_gap_stops_coverage_instead_of_extending_a_cheap_price():
    slots = (
        PriceSlot(NOW, NOW + timedelta(minutes=15), 1),
        PriceSlot(NOW + timedelta(hours=1), NOW + timedelta(hours=2), 5),
    )
    assert contiguous_prices(slots, NOW) == slots[:1]
    parsed = parse_price_slots(
        {
            "prices": [
                {"start": NOW.isoformat(), "price": 1},
                {"start": (NOW + timedelta(hours=4)).isoformat(), "price": 5},
            ]
        },
        NOW,
    )
    assert parsed[0].end <= NOW + timedelta(hours=1)


def test_non_finite_price_cannot_create_a_plan():
    assert parse_price_slots({"prices": [{"start": NOW.isoformat(), "price": "NaN"}]}, NOW) == ()
