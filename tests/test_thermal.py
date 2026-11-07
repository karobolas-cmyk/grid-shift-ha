"""Bounded real thermostat writes, ownership, readback, and shared phase capacity."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from custom_components.gridshift_battery.const import DEFAULTS
from custom_components.gridshift_battery.models import PriceSlot
from custom_components.gridshift_battery.thermal import ThermalController, thermal_target

NOW = datetime(2026, 11, 7, 16, tzinfo=UTC)
RULE = {
    "entity_id": "climate.living_room",
    "baseline": 21,
    "max_deviation": 1,
    "weight": 10,
    "mode": "scale",
    "direction": "auto",
    "heater_type": "air_air",
    "scop": 3.5,
    "max_power_w": 1000,
    "phase": "L1",
}
SLOTS = (
    PriceSlot(NOW, NOW + timedelta(minutes=15), 1),
    PriceSlot(NOW + timedelta(minutes=15), NOW + timedelta(minutes=30), 5),
)


def setup(enabled=False, acknowledge=True, rules=None):
    states = {
        "climate.living_room": SimpleNamespace(
            state="heat",
            last_reported=NOW,
            attributes={
                "current_temperature": 21,
                "temperature": 21,
                "min_temp": 7,
                "max_temp": 30,
                "target_temp_step": 0.5,
            },
        ),
        "sensor.outdoor": SimpleNamespace(state="5", last_reported=NOW, attributes={}),
    }
    calls = []

    async def call(domain, service, data, **kwargs):
        calls.append((domain, service, data))
        if acknowledge:
            states[data["entity_id"]].attributes.update(data)

    async def save():
        pass

    hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get),
        services=SimpleNamespace(
            has_service=lambda domain, service: domain == "climate",
            async_call=call,
        ),
    )
    storage = SimpleNamespace(
        state=SimpleNamespace(
            thermal_enabled=enabled,
            thermal_state={},
        ),
        async_save=save,
    )
    config = {
        **DEFAULTS,
        "outdoor_temperature_entity": "sensor.outdoor",
        "climate_rules": rules if rules is not None else [RULE],
    }
    return ThermalController(hass, config, storage), states, calls


def evaluate(controller, now=NOW, slots=SLOTS, currents=(1, 2, 3), stale=()):
    asyncio.run(controller.async_evaluate(slots, now, currents, stale))


def test_thermostat_control_is_off_until_separately_enabled():
    controller, _, calls = setup()
    evaluate(controller)
    assert calls == []


def test_only_selected_zone_is_written_within_its_temperature_band():
    controller, _, calls = setup(enabled=True)
    evaluate(controller)
    assert len(calls) == 1
    assert calls[0] == (
        "climate",
        "set_temperature",
        {"entity_id": "climate.living_room", "temperature": 22.0},
    )
    assert controller.snapshot["zones"][0]["status"] == "confirmed"
    assert controller.phase_reservations(NOW)[0] > 4


def test_hvac_off_is_preserved_and_not_turned_on():
    controller, states, calls = setup(enabled=True)
    states["climate.living_room"].state = "off"
    evaluate(controller)
    assert calls == []


def test_stale_phase_data_or_full_phase_prevents_increasing_heating():
    controller, _, calls = setup(enabled=True)
    evaluate(controller, stale=("sensor.l1",))
    assert calls == [] and controller.snapshot["zones"][0]["status"] == "phase_limit"
    evaluate(controller, currents=(19, 2, 3))
    assert calls == []


def test_manual_temperature_change_is_not_overwritten_or_restored():
    controller, states, calls = setup(enabled=True)
    evaluate(controller)
    states["climate.living_room"].attributes["temperature"] = 20
    evaluate(controller, NOW + timedelta(minutes=6))
    assert len(calls) == 1
    assert controller.snapshot["zones"][0]["status"] == "manual_override"
    asyncio.run(controller.async_set_enabled(False, NOW + timedelta(minutes=7)))
    assert states["climate.living_room"].attributes["temperature"] == 20


def test_disable_restores_the_previous_target_if_still_owned():
    controller, states, calls = setup(enabled=True)
    evaluate(controller)
    asyncio.run(controller.async_set_enabled(False, NOW + timedelta(minutes=1)))
    assert len(calls) == 2
    assert states["climate.living_room"].attributes["temperature"] == 21


def test_missing_prices_returns_an_owned_target_to_baseline():
    controller, states, calls = setup(enabled=True)
    evaluate(controller)
    evaluate(controller, NOW + timedelta(minutes=6), slots=())
    assert len(calls) == 2
    assert states["climate.living_room"].attributes["temperature"] == 21


def test_service_success_without_matching_target_is_not_confirmed():
    controller, _, calls = setup(enabled=True, acknowledge=False)
    evaluate(controller)
    assert len(calls) == 1
    assert controller.snapshot["zones"][0]["status"] == "awaiting_readback"
    evaluate(controller, NOW + timedelta(minutes=6))
    assert len(calls) == 1
    assert controller.snapshot["zones"][0]["status"] == "readback_unconfirmed"


def test_expensive_cooling_setback_raises_target_and_hydronic_has_no_precooling():
    _, states, _ = setup()
    entity = states["climate.living_room"]
    entity.state = "cool"
    expensive = tuple(PriceSlot(row.start, row.end, 6 - row.price) for row in SLOTS)
    assert thermal_target(RULE, entity, expensive, NOW, 30)["temperature"] == 22
    rule = {**RULE, "heater_type": "air_water"}
    assert thermal_target(rule, entity, SLOTS, NOW, 30)["temperature"] == 21


def test_stale_indoor_sensor_never_receives_a_new_target():
    controller, states, calls = setup(enabled=True)
    states["climate.living_room"].last_reported = NOW - timedelta(hours=3)
    evaluate(controller)
    assert calls == []
    assert controller.snapshot["zones"][0]["status"] == "stale_temperature"


def test_multiple_zones_do_not_write_or_start_together():
    second = {**RULE, "entity_id": "climate.bedroom"}
    controller, states, calls = setup(enabled=True, rules=[RULE, second])
    states["climate.bedroom"] = SimpleNamespace(
        state="heat",
        last_reported=NOW,
        attributes=dict(states["climate.living_room"].attributes),
    )
    evaluate(controller)
    assert len(calls) == 1
    assert controller.snapshot["zones"][1]["status"] == "rate_limited"
