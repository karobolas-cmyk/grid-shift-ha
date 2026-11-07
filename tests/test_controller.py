"""Tests for the Fronius mode-settle-write sequence."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.gridshift_battery import controller as controller_module
from custom_components.gridshift_battery.const import (
    CONF_AUTO_MODE_OPTION,
    CONF_BLOCK_DISCHARGE_MODE_OPTION,
    CONF_CHARGE_MODE_OPTION,
    CONF_CHARGE_POWER_ENTITY,
    CONF_CONTROL_MODE_ENTITY,
    CONF_DISCHARGE_MODE_OPTION,
    CONF_DISCHARGE_POWER_ENTITY,
    CONF_MODE_SETTLE_SECONDS,
)
from custom_components.gridshift_battery.controller import BatteryController, PowerReadbackError
from custom_components.gridshift_battery.models import Decision


class FakeState:
    def __init__(self, state: str) -> None:
        self.state = state
        self.last_reported = datetime.now(UTC)


class FakeStates:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = {entity_id: FakeState(value) for entity_id, value in values.items()}

    def get(self, entity_id: str):
        return self.values.get(entity_id)


class FakeServices:
    def __init__(self, states: FakeStates, events: list[tuple]) -> None:
        self.states = states
        self.events = events

    async def async_call(self, domain, service, data, blocking=False) -> None:
        self.events.append(("service", domain, service, dict(data)))
        if domain == "select":
            self.states.values[data["entity_id"]].state = data["option"]
        elif domain == "number":
            self.states.values[data["entity_id"]] = FakeState(str(data["value"]))


class FakeStorage:
    def __init__(self, *, discharge_enabled: bool = False) -> None:
        self.state = SimpleNamespace(
            enabled=True,
            owns_control=False,
            discharge_enabled=discharge_enabled,
            forced_action=None,
            forced_until=None,
            plan_overrides={},
        )

    async def async_save(self) -> None:
        return None


def config() -> dict[str, object]:
    return {
        CONF_CONTROL_MODE_ENTITY: "select.storage_mode",
        CONF_CHARGE_POWER_ENTITY: "number.grid_charge_power",
        CONF_DISCHARGE_POWER_ENTITY: "number.grid_discharge_power",
        CONF_CHARGE_MODE_OPTION: "Charge from Grid",
        CONF_BLOCK_DISCHARGE_MODE_OPTION: "Block Discharging",
        CONF_DISCHARGE_MODE_OPTION: "Discharge to Grid",
        CONF_AUTO_MODE_OPTION: "Auto",
        CONF_MODE_SETTLE_SECONDS: 20,
    }


def controller(*, discharge_enabled: bool = False):
    events: list[tuple] = []
    states = FakeStates(
        {
            "select.storage_mode": "Auto",
            "number.grid_charge_power": "0",
            "number.grid_discharge_power": "0",
        }
    )
    hass = SimpleNamespace(states=states, services=FakeServices(states, events))
    storage = FakeStorage(discharge_enabled=discharge_enabled)
    return BatteryController(hass, config(), storage), events


def test_charge_changes_mode_waits_then_writes_power() -> None:
    actuator, events = controller()
    original_sleep = controller_module.asyncio.sleep

    async def fake_sleep(seconds: float) -> None:
        events.append(("sleep", seconds))

    controller_module.asyncio.sleep = fake_sleep
    try:
        asyncio.run(
            actuator.async_apply(Decision("charge", 2345, "test_charge"))
        )
    finally:
        controller_module.asyncio.sleep = original_sleep

    assert events[0][1:3] == ("select", "select_option")
    assert events[0][3]["option"] == "Charge from Grid"
    assert events[1] == ("sleep", 20.0)
    assert events[2][1:3] == ("number", "set_value")
    assert events[2][3] == {"entity_id": "number.grid_charge_power", "value": 2340}
    assert actuator.execution["status"] == "confirmed"
    assert actuator.execution["readback_power_w"] == 2340
    assert actuator.execution["power_write_confirmed"] is True


def test_hold_changes_mode_waits_without_writing_power() -> None:
    actuator, events = controller()
    original_sleep = controller_module.asyncio.sleep

    async def fake_sleep(seconds: float) -> None:
        events.append(("sleep", seconds))

    controller_module.asyncio.sleep = fake_sleep
    try:
        asyncio.run(
            actuator.async_apply(Decision("hold", 0, "battery_reserved_for_later_peak"))
        )
    finally:
        controller_module.asyncio.sleep = original_sleep

    assert events[0][1:3] == ("select", "select_option")
    assert events[0][3]["option"] == "Block Discharging"
    assert events[1] == ("sleep", 20.0)
    assert len(events) == 2


def test_release_only_returns_to_auto() -> None:
    actuator, events = controller()
    actuator.storage.state.owns_control = True
    actuator.hass.states.values["select.storage_mode"].state = "Charge from Grid"
    asyncio.run(actuator.async_release_control())

    assert events == [
        (
            "service",
            "select",
            "select_option",
            {"entity_id": "select.storage_mode", "option": "Auto"},
        )
    ]


def test_discharge_uses_its_own_mode_and_power_entity() -> None:
    actuator, events = controller(discharge_enabled=True)
    original_sleep = controller_module.asyncio.sleep

    async def fake_sleep(seconds: float) -> None:
        events.append(("sleep", seconds))

    controller_module.asyncio.sleep = fake_sleep
    try:
        asyncio.run(
            actuator.async_apply(
                Decision(
                    "discharge",
                    0,
                    "test_discharge",
                    discharge_power_w=3219,
                )
            )
        )
    finally:
        controller_module.asyncio.sleep = original_sleep

    assert events[0][3]["option"] == "Discharge to Grid"
    assert events[1] == ("sleep", 20.0)
    assert events[2][3] == {
        "entity_id": "number.grid_discharge_power",
        "value": 3210,
    }


def test_rate_update_reuses_already_settled_mode() -> None:
    actuator, events = controller()
    actuator.hass.states.values["select.storage_mode"].state = "Charge from Grid"
    asyncio.run(actuator.async_apply(Decision("charge", 1789, "test_update")))

    assert events == [
        (
            "service",
            "number",
            "set_value",
            {"entity_id": "number.grid_charge_power", "value": 1780},
        )
    ]


def test_rate_update_waits_for_transiently_unavailable_control() -> None:
    actuator, events = controller()
    actuator.hass.states.values["select.storage_mode"].state = "Charge from Grid"
    actuator.hass.states.values["number.grid_charge_power"].state = "unavailable"
    original_sleep = controller_module.asyncio.sleep

    async def fake_sleep(seconds: float) -> None:
        events.append(("sleep", seconds))
        actuator.hass.states.values["number.grid_charge_power"].state = "0"

    controller_module.asyncio.sleep = fake_sleep
    try:
        asyncio.run(actuator.async_apply(Decision("charge", 1789, "test_update")))
    finally:
        controller_module.asyncio.sleep = original_sleep

    assert events == [
        ("sleep", 0.5),
        (
            "service",
            "number",
            "set_value",
            {"entity_id": "number.grid_charge_power", "value": 1780},
        ),
    ]


def test_rate_update_reports_unavailable_control_timeout() -> None:
    actuator, events = controller()
    actuator.config[CONF_MODE_SETTLE_SECONDS] = 1
    actuator.hass.states.values["select.storage_mode"].state = "Charge from Grid"
    actuator.hass.states.values["number.grid_charge_power"].state = "unavailable"
    original_sleep = controller_module.asyncio.sleep

    async def fake_sleep(seconds: float) -> None:
        events.append(("sleep", seconds))

    controller_module.asyncio.sleep = fake_sleep
    try:
        with pytest.raises(RuntimeError, match="stayed unavailable for 1 seconds"):
            asyncio.run(
                actuator.async_apply(Decision("charge", 1789, "test_update"))
            )
    finally:
        controller_module.asyncio.sleep = original_sleep

    assert events[:2] == [("sleep", 0.5), ("sleep", 0.5)]
    assert events[2][3]["option"] == "Auto"
    assert actuator.execution["failed_stage"] == "wait_power_entity"


def test_mode_change_cannot_confirm_an_inherited_power_value(monkeypatch) -> None:
    actuator, events = controller()
    actuator.hass.states.values["number.grid_charge_power"] = FakeState("3263")
    original_call = actuator.hass.services.async_call

    async def ignore_number_write(domain, service, data, blocking=False) -> None:
        if domain == "number":
            events.append(("service", domain, service, dict(data)))
            # Fronius reports its remembered rate after the new mode is active.
            actuator.hass.states.values[data["entity_id"]] = FakeState("3263")
            return
        await original_call(domain, service, data, blocking=blocking)

    async def no_sleep(seconds) -> None:
        return None

    monkeypatch.setattr(actuator.hass.services, "async_call", ignore_number_write)
    monkeypatch.setattr(controller_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(controller_module, "POWER_READBACK_TIMEOUT_SECONDS", 1)
    with pytest.raises(PowerReadbackError, match="Requested 10000 W.*3263"):
        asyncio.run(actuator.async_apply(Decision("charge", 10000, "test_charge")))

    assert actuator.execution["status"] == "failed"
    assert actuator.execution["failed_stage"] == "verify_power"
    assert actuator.execution["service_call_succeeded"] is True
    assert actuator.execution["power_write_confirmed"] is False
    assert actuator.execution["fallback_mode"] == "Auto"
    assert actuator.last_power_w == 0


def test_old_matching_value_without_a_new_report_is_not_confirmation(monkeypatch) -> None:
    actuator, _ = controller()
    actuator.hass.states.values["select.storage_mode"].state = "Charge from Grid"
    actuator.hass.states.values["number.grid_charge_power"] = FakeState("2340")
    original_call = actuator.hass.services.async_call

    async def no_number_report(domain, service, data, blocking=False) -> None:
        if domain != "number":
            await original_call(domain, service, data, blocking=blocking)

    async def no_sleep(seconds) -> None:
        return None

    monkeypatch.setattr(actuator.hass.services, "async_call", no_number_report)
    monkeypatch.setattr(controller_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(controller_module, "POWER_READBACK_TIMEOUT_SECONDS", 1)
    with pytest.raises(PowerReadbackError, match="fresh after write: False"):
        asyncio.run(actuator.async_apply(Decision("charge", 2340, "test_charge")))


def test_delayed_matching_readback_confirms_the_requested_rate(monkeypatch) -> None:
    actuator, _ = controller()
    actuator.hass.states.values["select.storage_mode"].state = "Charge from Grid"
    actuator.hass.states.values["number.grid_charge_power"] = FakeState("3263")

    async def no_number_report(domain, service, data, blocking=False) -> None:
        return None

    async def publish_after_sleep(seconds) -> None:
        actuator.hass.states.values["number.grid_charge_power"] = FakeState("10000")

    monkeypatch.setattr(actuator.hass.services, "async_call", no_number_report)
    monkeypatch.setattr(controller_module.asyncio, "sleep", publish_after_sleep)
    asyncio.run(actuator.async_apply(Decision("charge", 10000, "test_charge")))

    assert actuator.execution["status"] == "confirmed"
    assert actuator.execution["readback_fresh"] is True
    assert actuator.last_power_w == 10000


def test_failed_number_service_is_not_hidden_by_a_positive_rate(monkeypatch) -> None:
    actuator, _ = controller()
    actuator.hass.states.values["select.storage_mode"].state = "Charge from Grid"
    actuator.hass.states.values["number.grid_charge_power"] = FakeState("3263")
    original_call = actuator.hass.services.async_call

    async def fail_number(domain, service, data, blocking=False) -> None:
        if domain == "number":
            raise RuntimeError("number service refused the write")
        await original_call(domain, service, data, blocking=blocking)

    monkeypatch.setattr(actuator.hass.services, "async_call", fail_number)
    with pytest.raises(RuntimeError, match="number service refused"):
        asyncio.run(actuator.async_apply(Decision("charge", 10000, "test_charge")))

    assert actuator.execution["failed_stage"] == "write_power"
    assert actuator.execution["service_call_succeeded"] is False
    assert actuator.execution["status"] == "failed"
    assert actuator.execution["fallback_mode"] == "Auto"


def test_disabling_during_settle_prevents_the_power_write(monkeypatch) -> None:
    actuator, events = controller()

    async def disable_during_sleep(seconds) -> None:
        await actuator.async_set_enabled(False)

    monkeypatch.setattr(controller_module.asyncio, "sleep", disable_during_sleep)
    with pytest.raises(RuntimeError, match="disabled or Fronius mode changed"):
        asyncio.run(actuator.async_apply(Decision("charge", 10000, "test_charge")))

    assert not any(event[1] == "number" for event in events)


def test_disarming_grid_discharge_only_returns_to_auto() -> None:
    actuator, events = controller(discharge_enabled=True)
    actuator.storage.state.owns_control = True
    actuator.hass.states.values["select.storage_mode"].state = "Discharge to Grid"
    asyncio.run(actuator.async_set_discharge_enabled(False))

    assert events == [
        (
            "service",
            "select",
            "select_option",
            {"entity_id": "select.storage_mode", "option": "Auto"},
        )
    ]
    assert actuator.discharge_enabled is False

def test_force_action_is_stored_and_can_be_cleared() -> None:
    actuator, _ = controller()

    asyncio.run(actuator.async_set_force_action("charge", 5))

    assert actuator.forced_action == "charge"
    assert actuator.forced_until is not None

    asyncio.run(actuator.async_clear_force_action())

    assert actuator.forced_action is None
    assert actuator.forced_until is None


def test_plan_override_survives_quarter_boundary() -> None:
    actuator, _ = controller()
    now = datetime.now(UTC)
    next_quarter = now + timedelta(minutes=1)

    asyncio.run(
        actuator.async_set_plan_overrides({next_quarter.isoformat(): "charge"})
    )

    assert asyncio.run(
        actuator.async_active_plan_action(next_quarter + timedelta(minutes=1))
    ) == "charge"


def test_expired_plan_overrides_are_pruned() -> None:
    actuator, _ = controller()
    now = datetime.now(UTC)
    old_quarter = now - timedelta(minutes=20)
    actuator.storage.state.plan_overrides = {old_quarter.isoformat(): "charge"}

    assert asyncio.run(actuator.async_active_plan_action(now)) is None
    assert actuator.plan_overrides == {}


