"""Tests for the Fronius mode-settle-write sequence."""

import asyncio
from types import SimpleNamespace

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
from custom_components.gridshift_battery.controller import BatteryController
from custom_components.gridshift_battery.models import Decision


class FakeState:
    def __init__(self, state: str) -> None:
        self.state = state


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


class FakeStorage:
    def __init__(self, *, discharge_enabled: bool = False) -> None:
        self.state = SimpleNamespace(
            enabled=True,
            owns_control=False,
            discharge_enabled=discharge_enabled,
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
