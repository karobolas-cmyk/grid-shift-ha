"""Bounded real thermostat writes, ownership, readback, and shared phase capacity."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import ServiceValidationError

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


def test_empty_climate_configuration_returns_a_validation_error_without_enabling():
    controller, _, calls = setup(rules=[])
    assert controller.snapshot["configured_zone_count"] == 0
    with pytest.raises(ServiceValidationError, match="No thermostat zones are saved"):
        asyncio.run(controller.async_set_enabled(True, NOW))
    assert controller.enabled is False
    assert calls == []


def test_saved_zone_count_is_available_before_and_after_the_first_evaluation():
    controller, _, calls = setup()
    assert controller.snapshot["configured_zone_count"] == 1
    asyncio.run(controller.async_set_enabled(True, NOW))
    assert controller.enabled is True
    evaluate(controller)
    assert controller.snapshot["configured_zone_count"] == 1
    assert len(calls) == 1


def test_empty_climate_configuration_can_always_be_disabled():
    controller, _, calls = setup(enabled=True, rules=[])
    asyncio.run(controller.async_set_enabled(False, NOW))
    assert controller.enabled is False
    assert calls == []


@pytest.mark.parametrize("policy,expensive,expected", [
    ("both", False, 38), ("both", True, 35),
    ("setback_only", False, 37), ("setback_only", True, 35),
    ("lower_only", False, 37), ("lower_only", True, 35),
])
def test_spa_at_37_degrees_keeps_device_limits_and_temperature_policy(policy, expensive, expected):
    rule = {**RULE, "baseline": 37, "max_deviation": 2, "adjustment": policy}
    controller, states, calls = setup(enabled=True, rules=[rule])
    states[RULE["entity_id"]].attributes.update(
        temperature=37, current_temperature=37, min_temp=10, max_temp=38,
    )
    slots = tuple(
        PriceSlot(slot.start, slot.end, 6 - slot.price if expensive else slot.price)
        for slot in SLOTS
    )
    evaluate(controller, slots=slots)
    assert controller.snapshot["zones"][0]["target"] == {"temperature": expected}
    if expected == 37:
        assert calls == []
    else:
        assert calls == [("climate", "set_temperature", {
            "entity_id": RULE["entity_id"], "temperature": expected,
        })]


def test_physical_write_rechecks_device_limits_after_persistence():
    rule = {**RULE, "baseline": 37}
    controller, states, calls = setup(enabled=True, rules=[rule])
    state = states[RULE["entity_id"]]
    state.attributes.update(temperature=37, current_temperature=37, min_temp=10, max_temp=40)

    async def save():
        state.attributes["max_temp"] = 37

    controller.storage.async_save = save
    zone = {}
    asyncio.run(controller._write(RULE["entity_id"], {"temperature": 38}, NOW, zone))
    assert calls == []
    assert zone["status"] == "target_outside_device_limits"


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


@pytest.mark.parametrize("policy", ["setback_only", "lower_only"])
def test_restricted_heating_never_preheats_above_baseline(policy):
    rule = {**RULE, "adjustment": policy}
    controller, states, calls = setup(enabled=True, rules=[rule])
    evaluate(controller)
    assert calls == []
    assert thermal_target(rule, states[RULE["entity_id"]], SLOTS, NOW, 5)["temperature"] == 21


def test_setback_can_restore_baseline_but_never_overheat():
    rule = {**RULE, "adjustment": "setback_only"}
    controller, states, calls = setup(enabled=True, rules=[rule])
    expensive = tuple(PriceSlot(row.start, row.end, 6 - row.price) for row in SLOTS)
    evaluate(controller, slots=expensive)
    assert states[RULE["entity_id"]].attributes["temperature"] == 20
    evaluate(controller, NOW + timedelta(minutes=6), slots=SLOTS)
    assert [call[2]["temperature"] for call in calls] == [20, 21]


@pytest.mark.parametrize("missing", ["none", "prices", "weather"])
def test_lower_only_does_not_recover_automatically_when_prices_fall_or_data_disappears(missing):
    controller, states, calls = setup(enabled=True, rules=[{**RULE, "adjustment": "lower_only"}])
    expensive = tuple(PriceSlot(row.start, row.end, 6 - row.price) for row in SLOTS)
    evaluate(controller, slots=expensive)
    assert states[RULE["entity_id"]].attributes["temperature"] == 20
    if missing == "weather":
        states.pop("sensor.outdoor")
    evaluate(controller, NOW + timedelta(minutes=6), slots=() if missing == "prices" else SLOTS)
    assert [call[2]["temperature"] for call in calls] == [20]


def test_lower_only_disable_leaves_temperature_for_manual_restoration():
    controller, states, calls = setup(enabled=True, rules=[{**RULE, "adjustment": "lower_only"}])
    expensive = tuple(PriceSlot(row.start, row.end, 6 - row.price) for row in SLOTS)
    evaluate(controller, slots=expensive)
    asyncio.run(controller.async_set_enabled(False, NOW + timedelta(minutes=1)))
    assert [call[2]["temperature"] for call in calls] == [20]
    assert states[RULE["entity_id"]].attributes["temperature"] == 20
    zone = controller.storage.state.thermal_state[RULE["entity_id"]]
    assert zone["status"] == "manual_restore_required"
    assert "original" not in zone


def test_lower_only_policy_survives_restart_and_zone_removal():
    controller, _, calls = setup(enabled=True, rules=[{**RULE, "adjustment": "lower_only"}])
    expensive = tuple(PriceSlot(row.start, row.end, 6 - row.price) for row in SLOTS)
    evaluate(controller, slots=expensive)
    restored = ThermalController(
        controller.hass, {**controller.config, "climate_rules": []}, controller.storage,
    )
    asyncio.run(restored.async_release(NOW + timedelta(minutes=1)))
    assert [call[2]["temperature"] for call in calls] == [20]


def test_lower_only_does_not_increase_a_lower_manual_setting_or_decrease_below_its_band():
    rule = {**RULE, "adjustment": "lower_only"}
    _, states, _ = setup()
    entity = states[RULE["entity_id"]]
    entity.attributes["temperature"] = 19
    assert thermal_target(rule, entity, SLOTS, NOW, 5)["temperature"] == 19


@pytest.mark.parametrize("direction", ["cool", "heat_cool"])
def test_lower_only_does_not_turn_into_extra_cooling(direction):
    controller, states, calls = setup(enabled=True, rules=[{**RULE, "adjustment": "lower_only"}])
    states[RULE["entity_id"]].state = direction
    evaluate(controller)
    assert calls == []
    assert controller.snapshot["zones"][0]["status"] == "lower_only_requires_heating"


def test_step_rounding_never_raises_above_baseline_or_current_target():
    _, states, _ = setup()
    entity = states[RULE["entity_id"]]
    entity.attributes["temperature"] = 20.5
    rule = {**RULE, "baseline": 20.3, "adjustment": "setback_only"}
    assert thermal_target(rule, entity, SLOTS, NOW, 5)["temperature"] == 20
    rule = {**RULE, "adjustment": "lower_only"}
    assert thermal_target(rule, entity, SLOTS, NOW, 5)["temperature"] <= 20.5


def test_final_lower_only_guard_rechecks_a_manual_change_during_persistence():
    controller, states, calls = setup(enabled=True, rules=[{**RULE, "adjustment": "lower_only"}])
    entity = states[RULE["entity_id"]]

    async def save():
        entity.attributes["temperature"] = 20

    controller.storage.async_save = save
    asyncio.run(controller._write(RULE["entity_id"], {"temperature": 21}, NOW, {}))
    assert calls == []


def test_lower_only_preserves_a_manual_temperature_restoration():
    controller, states, calls = setup(enabled=True, rules=[{**RULE, "adjustment": "lower_only"}])
    expensive = tuple(PriceSlot(row.start, row.end, 6 - row.price) for row in SLOTS)
    evaluate(controller, slots=expensive)
    states[RULE["entity_id"]].attributes["temperature"] = 21
    evaluate(controller, NOW + timedelta(minutes=6), slots=expensive)
    assert len(calls) == 1
    assert controller.snapshot["zones"][0]["status"] == "manual_override"


def test_lower_only_release_in_cooling_does_not_leave_a_failed_restore_loop():
    controller, states, calls = setup(enabled=True, rules=[{**RULE, "adjustment": "lower_only"}])
    expensive = tuple(PriceSlot(row.start, row.end, 6 - row.price) for row in SLOTS)
    evaluate(controller, slots=expensive)
    states[RULE["entity_id"]].state = "cool"
    asyncio.run(controller.async_set_enabled(False, NOW + timedelta(minutes=1)))
    assert len(calls) == 1
    zone = controller.storage.state.thermal_state[RULE["entity_id"]]
    assert zone["status"] == "manual_restore_required"
    assert "original" not in zone


def test_no_preheating_policy_also_prevents_precooling():
    _, states, _ = setup()
    entity = states[RULE["entity_id"]]
    entity.state = "cool"
    target = thermal_target({**RULE, "adjustment": "setback_only"}, entity, SLOTS, NOW, 30)
    assert target["temperature"] >= RULE["baseline"]


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
