"""Exercise real service and unload handlers through upgrade/reload cycles."""

import asyncio
import importlib.util
import sys
from copy import deepcopy
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from test_bridge import States, valid_configuration
from test_controller import controller
from test_coordinator import coordinator_class

from custom_components.gridshift_battery.const import DOMAIN
from custom_components.gridshift_battery.controller import RuntimeStorage


def integration(monkeypatch):
    coordinator_class(monkeypatch)
    # HA validates the transport schema before dispatch; these tests exercise the
    # actual handler and its real validate_configuration, with only registration stubbed.
    vol = ModuleType("voluptuous")
    vol.Schema = lambda schema: schema
    vol.Required = lambda name: name
    vol.In = lambda values: values
    vol.All = lambda *validators: validators
    vol.Coerce = lambda kind: kind
    vol.Range = lambda **bounds: bounds
    monkeypatch.setitem(sys.modules, "voluptuous", vol)
    core = sys.modules["homeassistant.core"]
    for key, value in {"callback": lambda fn: fn, "Event": object, "ServiceCall": object}.items():
        monkeypatch.setattr(core, key, value, raising=False)
    monkeypatch.setattr(sys.modules["homeassistant.const"], "Platform",
                        SimpleNamespace(SENSOR="sensor", SWITCH="switch"), raising=False)
    event = ModuleType("homeassistant.helpers.event")
    event.async_track_state_change_event = lambda *args, **kwargs: lambda: None
    event.async_track_time_change = lambda *args, **kwargs: lambda: None
    monkeypatch.setitem(sys.modules, event.__name__, event)
    name = "custom_components.gridshift_battery"
    path = Path(__file__).parents[1] / "custom_components/gridshift_battery/__init__.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


class MemoryStore:
    def __init__(self, raw):
        self.raw = deepcopy(raw)

    async def async_save(self, raw):
        self.raw = deepcopy(raw)


def runtime(item, *, enabled=True, discharge=True, fault=None):
    actuator, events = controller(discharge_enabled=discharge)
    store = MemoryStore({"enabled": enabled, "discharge_enabled": discharge,
                         "thermal_enabled": enabled, "control_fault": fault or {}})
    storage = RuntimeStorage(store, store.raw)
    actuator.storage = storage

    async def release(now):
        pass

    async def set_thermal(value, now):
        storage.state.thermal_enabled = value

    thermal = SimpleNamespace(async_release=release, async_set_enabled=set_thermal)
    coordinator = SimpleNamespace(stopping=False, update_tasks=set(), notification_task=None,
                                  price_task=None, thermal_task=None, thermal=thermal)
    return item.GridShiftRuntime(coordinator, actuator, storage, True), store, events


def services_hass(item, rt, *, unload_ok=True):
    handlers, updates = {}, []
    config = valid_configuration()
    entry = SimpleNamespace(entry_id="home", data=config, options={})

    def register(domain, name, handler, **kwargs):
        handlers[name] = handler

    def update(entry, **kwargs):
        updates.append(kwargs)
        entry.data, entry.options = kwargs["data"], kwargs["options"]

    async def unload(*args):
        return unload_ok

    hass = SimpleNamespace(
        states=States(), data={DOMAIN: {"home": rt}},
        services=SimpleNamespace(has_service=lambda *args: False, async_register=register),
        config_entries=SimpleNamespace(async_entries=lambda _: [entry], async_update_entry=update,
                                       async_unload_platforms=unload),
    )
    return hass, entry, handlers, updates


@pytest.mark.parametrize("enabled", [True, False])
def test_real_unload_keeps_on_or_off_state_and_export_choice(monkeypatch, enabled):
    item = integration(monkeypatch)

    async def run():
        rt, store, _ = runtime(item, enabled=enabled)
        hass, entry, _, _ = services_hass(item, rt)
        assert await item.async_unload_entry(hass, entry)
        reloaded = RuntimeStorage(store, store.raw)
        assert reloaded.state.enabled is enabled
        assert reloaded.state.discharge_enabled is True
        assert reloaded.state.thermal_enabled is enabled
        assert "home" not in hass.data[DOMAIN]

    asyncio.run(run())


def test_failed_platform_unload_resumes_the_existing_worker(monkeypatch):
    item = integration(monkeypatch)

    async def run():
        rt, store, _ = runtime(item)
        hass, entry, _, _ = services_hass(item, rt, unload_ok=False)
        assert await item.async_unload_entry(hass, entry) is False
        assert rt.controller.enabled is True
        assert rt.controller._can_write is True
        assert rt.coordinator.stopping is False
        assert store.raw["enabled"] is True
        assert hass.data[DOMAIN]["home"] is rt

    asyncio.run(run())


def test_same_configuration_is_a_noop_and_new_limits_keep_control_on(monkeypatch):
    item = integration(monkeypatch)

    async def run():
        rt, store, events = runtime(item)
        hass, entry, handlers, updates = services_hass(item, rt)
        await item.async_setup(hass, {})
        await handlers["apply_configuration"](SimpleNamespace(data={"configuration": entry.data}))
        assert updates == []
        changed = {**entry.data, "max_charge_power_w": 6000, "horizon_hours": 48}
        await handlers["apply_configuration"](SimpleNamespace(data={"configuration": changed}))
        assert updates and entry.data == changed
        assert rt.controller.enabled is True
        assert store.raw["enabled"] is True
        assert store.raw["discharge_enabled"] is True
        assert store.raw["thermal_enabled"] is True
        assert events == []

    asyncio.run(run())


def test_configuration_save_does_not_rearm_an_off_or_faulted_controller(monkeypatch):
    item = integration(monkeypatch)

    async def run():
        fault = {"reason": "control_paused_after_write_failure", "execution": {"paused": True}}
        rt, store, events = runtime(item, fault=fault)
        hass, entry, handlers, _ = services_hass(item, rt)
        await item.async_setup(hass, {})
        await handlers["apply_configuration"](SimpleNamespace(data={"configuration": {
            **entry.data, "horizon_hours": 48,
        }}))
        assert store.raw["enabled"] is False
        assert store.raw["control_fault"] == fault
        assert events == []

    asyncio.run(run())


def test_new_actuator_requires_a_new_opt_in(monkeypatch):
    item = integration(monkeypatch)

    async def run():
        rt, store, _ = runtime(item)
        hass, entry, handlers, _ = services_hass(item, rt)
        hass.states.values["number.new_charge"] = SimpleNamespace(
            state="unavailable", attributes={},
        )
        await item.async_setup(hass, {})
        await handlers["apply_configuration"](SimpleNamespace(data={"configuration": {
            **entry.data, "charge_power_entity": "number.new_charge",
        }}))
        assert store.raw["enabled"] is False
        assert store.raw["discharge_enabled"] is False

    asyncio.run(run())


def test_real_fault_remains_paused_after_unload_and_reload(monkeypatch):
    item = integration(monkeypatch)

    async def run():
        fault = {"reason": "control_paused_after_write_failure", "execution": {"paused": True}}
        rt, store, events = runtime(item, fault=fault)
        hass, entry, _, _ = services_hass(item, rt)
        assert await item.async_unload_entry(hass, entry)
        reloaded = RuntimeStorage(store, store.raw)
        assert reloaded.state.enabled is False
        assert reloaded.state.control_fault == fault
        assert events == []

    asyncio.run(run())


def test_adding_an_export_control_does_not_disarm_existing_battery_control(monkeypatch):
    item = integration(monkeypatch)

    async def run():
        rt, store, _ = runtime(item)
        hass, entry, handlers, _ = services_hass(item, rt)
        hass.states.values["number.new_discharge"] = SimpleNamespace(
            state="unavailable", attributes={},
        )
        await item.async_setup(hass, {})
        await handlers["apply_configuration"](SimpleNamespace(data={"configuration": {
            **entry.data, "discharge_power_entity": "number.new_discharge",
        }}))
        assert store.raw["enabled"] is True
        assert store.raw["discharge_enabled"] is False

    asyncio.run(run())


def test_reload_drains_all_old_updates_before_a_new_worker_can_load(monkeypatch):
    item = integration(monkeypatch)

    async def run():
        rt, store, _ = runtime(item)
        hass, entry, _, _ = services_hass(item, rt)
        gates = [asyncio.Event(), asyncio.Event()]
        finished = []

        async def old_update(index):
            await gates[index].wait()
            await rt.storage.async_save()
            finished.append(index)

        tasks = [asyncio.create_task(old_update(index)) for index in range(2)]
        rt.coordinator.update_tasks.update(tasks)
        shutdown = asyncio.create_task(item.async_unload_entry(hass, entry))
        await asyncio.sleep(0)
        assert rt.coordinator.stopping is True
        assert not shutdown.done()
        gates[0].set()
        await tasks[0]
        assert not shutdown.done()
        gates[1].set()
        assert await shutdown
        assert finished == [0, 1]
        assert store.raw["enabled"] is True

    asyncio.run(run())
