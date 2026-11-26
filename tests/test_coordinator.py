"""Exercise the real coordinator with small HA infrastructure stubs."""

import asyncio
import importlib
import sys
from datetime import UTC, datetime, timedelta
from types import ModuleType, SimpleNamespace

import pytest

from custom_components.gridshift_battery.controller import RuntimeStorage
from custom_components.gridshift_battery.models import Decision

NOW = datetime(2026, 11, 7, 16, tzinfo=UTC)


def coordinator_class(monkeypatch):
    class Coordinator:
        @classmethod
        def __class_getitem__(cls, _):
            return cls

        def __init__(self, hass, *args, **kwargs):
            self.hass = hass

    modules = {
        "homeassistant.config_entries": {"ConfigEntry": object},
        "homeassistant.const": {"ATTR_UNIT_OF_MEASUREMENT": "unit_of_measurement"},
        "homeassistant.helpers.update_coordinator": {
            "DataUpdateCoordinator": Coordinator,
            "UpdateFailed": RuntimeError,
        },
        "homeassistant.util": {"dt": SimpleNamespace(now=lambda: NOW)},
    }
    for name, values in modules.items():
        module = ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    name = "custom_components.gridshift_battery.coordinator"
    monkeypatch.delitem(sys.modules, name, raising=False)
    return importlib.import_module(name).GridShiftCoordinator


def climate_home(monkeypatch, mode="heat", price_now=5, price_next=1):
    cls = coordinator_class(monkeypatch)
    states = {
        f"sensor.l{index}": SimpleNamespace(
            state="1", last_reported=NOW - timedelta(minutes=3),
            attributes={"unit_of_measurement": "A"},
        )
        for index in (1, 2, 3)
    }
    states["climate.room"] = SimpleNamespace(
        state=mode, last_reported=NOW - timedelta(hours=7), attributes={
            "current_temperature": 21, "temperature": 21,
            "min_temp": 7, "max_temp": 30, "target_temp_step": 0.5,
        },
    )
    states["sensor.price"] = SimpleNamespace(
        state=str(price_now), attributes={"prices": [
            {"start": NOW.isoformat(), "end": (NOW + timedelta(minutes=15)).isoformat(),
             "price": price_now},
            {"start": (NOW + timedelta(minutes=15)).isoformat(),
             "end": (NOW + timedelta(minutes=30)).isoformat(), "price": price_next},
        ]},
    )
    for entity_id, value, unit in (
        ("sensor.soc", 50, "%"), ("sensor.grid", 1000, "W"),
        ("sensor.battery", 0, "W"),
    ):
        states[entity_id] = SimpleNamespace(
            state=str(value), last_reported=NOW, attributes={"unit_of_measurement": unit},
        )
    tasks, writes, releases = [], [], []

    def create_task(coroutine):
        task = asyncio.create_task(coroutine)
        tasks.append(task)
        return task

    async def save(data):
        pass

    async def release():
        releases.append(True)

    async def no_action(*args):
        pass

    async def call(domain, service, data, **kwargs):
        writes.append((domain, service, data))
        states[data["entity_id"]].attributes.update(data)

    hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get), async_create_task=create_task,
        services=SimpleNamespace(has_service=lambda *args: False, async_call=call),
    )
    config = {
        "price_entity": "sensor.price", "soc_entity": "sensor.soc",
        "house_load_entity": "sensor.grid", "house_load_source": "grid",
        "battery_power_entity": "sensor.battery",
        "phase_current_entities": ["sensor.l1", "sensor.l2", "sensor.l3"],
        "climate_rules": [{
            "entity_id": "climate.room", "baseline": 21, "max_deviation": 1,
            "weight": 10, "mode": "scale", "direction": "auto", "heater_type": "air_air",
            "scop": 3.5, "max_power_w": 1000, "phase": "L1",
        }],
    }
    controller = SimpleNamespace(
        enabled=True, last_power_w=0, discharge_enabled=False, plan_overrides={},
        async_release_control=release, async_active_force_action=no_action,
        async_active_plan_action=no_action, async_apply=no_action,
    )
    item = cls(
        hass, SimpleNamespace(data=config, options={}, entry_id="entry", title="Home"),
        RuntimeStorage(SimpleNamespace(async_save=save), {"thermal_enabled": True}), controller,
    )
    return item, states, tasks, writes, releases


@pytest.mark.parametrize("mode,price_now,price_next,target,status", [
    ("heat", 5, 1, 20, "confirmed"), ("cool", 5, 1, 22, "confirmed"),
    ("heat", 1, 5, None, "phase_limit"), ("cool", 1, 5, None, "phase_limit"),
])
def test_stale_phases_allow_climate_setback_but_block_more_demand(
    monkeypatch, mode, price_now, price_next, target, status,
):
    async def run():
        item, _, tasks, writes, releases = climate_home(monkeypatch, mode, price_now, price_next)
        decision = await item._async_update_data()
        assert decision.reason == "phase_sensor_stale"
        assert releases == [True]
        await asyncio.gather(*tasks)
        assert writes == ([] if target is None else [(
            "climate", "set_temperature", {"entity_id": "climate.room", "temperature": target},
        )])
        assert item.thermal.snapshot["zones"][0]["status"] == status
        assert item.thermal.snapshot["outdoor_temperature_assumed"] is True

    asyncio.run(run())


def test_climate_increase_resumes_when_phase_reports_recover(monkeypatch):
    async def run():
        item, states, tasks, writes, _ = climate_home(monkeypatch, price_now=1, price_next=5)
        await item._async_update_data()
        await asyncio.gather(*tasks)
        assert writes == []
        for index in (1, 2, 3):
            states[f"sensor.l{index}"].last_reported = NOW
        await item._async_update_data()
        await asyncio.gather(*tasks)
        assert writes == [(
            "climate", "set_temperature", {"entity_id": "climate.room", "temperature": 22},
        )]
        assert item.thermal.snapshot["zones"][0]["status"] == "confirmed"

    asyncio.run(run())


@pytest.mark.parametrize("low,high,expected_status", [
    (18, 24, "phase_limit"), (21, 23, "phase_limit"), (19, 21, "phase_limit"),
    (21, 22, "confirmed"),
])
def test_stale_phases_guard_each_side_of_combined_heating_and_cooling(
    monkeypatch, low, high, expected_status,
):
    async def run():
        item, states, tasks, writes, _ = climate_home(monkeypatch, mode="heat_cool")
        attributes = states["climate.room"].attributes
        attributes.pop("temperature")
        attributes.update(target_temp_low=low, target_temp_high=high)
        await item._async_update_data()
        await asyncio.gather(*tasks)
        assert item.thermal.snapshot["zones"][0]["status"] == expected_status
        assert writes == ([] if expected_status == "phase_limit" else [(
            "climate", "set_temperature", {
                "entity_id": "climate.room", "target_temp_low": 20, "target_temp_high": 22,
            },
        )])

    asyncio.run(run())


@pytest.mark.parametrize("stopping", [False, True])
def test_slow_prices_never_delay_battery_failsafe_or_write_after_stop(monkeypatch, stopping):
    async def run():
        item, _, tasks, writes, releases = climate_home(monkeypatch)
        original_prices = item.prices.async_prices
        gate = asyncio.Event()

        async def prices(now):
            await gate.wait()
            return await original_prices(now)

        item.prices.async_prices = prices
        decision = await item._async_update_data()
        assert decision.reason == "phase_sensor_stale"
        assert releases == [True]
        assert writes == []
        assert not item.price_task.done()
        item.stopping = stopping
        gate.set()
        await asyncio.gather(*tasks)
        assert len(writes) == (0 if stopping else 1)

    asyncio.run(run())


def test_unload_can_cancel_prices_and_drain_the_waiting_climate_worker(monkeypatch):
    async def run():
        item, _, tasks, writes, _ = climate_home(monkeypatch)
        gate = asyncio.Event()

        async def prices(now):
            await gate.wait()

        item.prices.async_prices = prices
        await item._async_update_data()
        await asyncio.sleep(0)
        item.stopping = True
        item.price_task.cancel()
        await item.thermal_task
        await asyncio.gather(*tasks, return_exceptions=True)
        assert writes == []
        assert not item.thermal_task.cancelled()

    asyncio.run(run())


def test_failed_price_fetch_does_not_claim_a_thermostat(monkeypatch):
    async def run():
        item, _, tasks, writes, _ = climate_home(monkeypatch)

        async def prices(now):
            raise RuntimeError("price provider offline")

        item.prices.async_prices = prices
        await item._async_update_data()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert item.thermal_task.exception() is None
        assert writes == []
        assert "original" not in item.storage.state.thermal_state["climate.room"]

    asyncio.run(run())


def test_reported_zero_phases_and_battery_supplied_house_still_plan(monkeypatch):
    cls = coordinator_class(monkeypatch)

    async def run():
        states = {}
        for entity_id, value, unit in (
            ("sensor.l1", 0, "A"),
            ("sensor.l2", 0, "A"),
            ("sensor.l3", 0, "A"),
            ("sensor.grid", 0, "W"),
            ("sensor.charge", 0, "W"),
            ("sensor.discharge", 2355, "W"),
            ("sensor.solar", 26, "W"),
            ("sensor.soc", 56, "%"),
        ):
            states[entity_id] = SimpleNamespace(
                state=str(value),
                last_reported=NOW,
                last_updated=NOW - timedelta(hours=3),
                attributes={"unit_of_measurement": unit},
            )
        states["sensor.price"] = SimpleNamespace(
            state="3.1",
            attributes={
                "prices": [
                    {
                        "start": NOW.isoformat(),
                        "end": (NOW + timedelta(minutes=15)).isoformat(),
                        "price": 3.1,
                    },
                    {
                        "start": (NOW + timedelta(minutes=15)).isoformat(),
                        "end": (NOW + timedelta(minutes=30)).isoformat(),
                        "price": 3.2,
                    },
                ]
            },
        )
        tasks, applied = [], []

        def create_task(coroutine):
            task = asyncio.create_task(coroutine)
            tasks.append(task)
            return task

        async def save(data):
            pass

        async def no_action(*args):
            return None

        async def apply(decision: Decision):
            applied.append(decision)

        config = {
            "price_entity": "sensor.price",
            "soc_entity": "sensor.soc",
            "house_load_entity": "sensor.grid",
            "house_load_source": "grid",
            "battery_charge_power_sensor": "sensor.charge",
            "battery_discharge_power_sensor": "sensor.discharge",
            "solar_power_entities": ["sensor.solar"],
            "phase_current_entities": ["sensor.l1", "sensor.l2", "sensor.l3"],
        }
        controller = SimpleNamespace(
            enabled=False,
            last_power_w=0,
            discharge_enabled=False,
            plan_overrides={},
            async_active_force_action=no_action,
            async_active_plan_action=no_action,
            async_apply=apply,
        )
        hass = SimpleNamespace(
            states=SimpleNamespace(get=states.get),
            async_create_task=create_task,
            services=SimpleNamespace(has_service=lambda *args: False),
        )
        item = cls(
            hass,
            SimpleNamespace(data=config, options={}, entry_id="entry", title="Home"),
            RuntimeStorage(SimpleNamespace(async_save=save), None),
            controller,
        )
        assert item._phase_currents(NOW) == ((0.0, 0.0, 0.0), 0, ())
        await item._async_update_data()
        await asyncio.gather(*tasks)
        assert applied and applied[0].reason != "price_forecast_unavailable"
        assert item.power_sources["house_consumption_w"] == 2381
        assert item._battery_power_w(NOW) == -2355
        assert item.power_snapshot["entities"]["sensor.charge"]["state"] == "0"
        assert item.prices.snapshot["status"] == "ready"
        assert item._guard_thermal_increase((2, 2, 2))
        assert item._guard_thermal_increase((6, 6, 6))
        controller.enabled = True
        controller.execution = {"action": "charge", "status": "pending",
                                "requested_power_w": 10000}
        assert not item._guard_thermal_increase((6, 6, 6))
        states["sensor.solarnet_power_load_consumed"] = SimpleNamespace(
            state="4210", last_reported=NOW,
            attributes={"unit_of_measurement": "W"},
        )
        item.config["house_consumption_entity"] = "sensor.solarnet_power_load_consumed"
        item.config["house_load_source"] = "grid"
        states["sensor.grid"].state = "288"
        states["sensor.discharge"].state = "3920"
        states["sensor.solar"].state = "0"
        controller.enabled = False
        await item._async_update_data()
        assert item.power_sources["house_consumption_w"] == 4210
        assert item.power_sources["house_source"] == "measured_total"
        assert not item.power_sources["house_is_grid"]
        assert item.power_snapshot["mapping"]["house_consumption_entity"] == (
            "sensor.solarnet_power_load_consumed"
        )
        # A phase fault must stop actuation immediately without hiding published prices.
        states["sensor.l2"].last_reported = NOW - timedelta(minutes=3)
        assert not item._guard_thermal_increase((2, 2, 2))
        stale_decision = await item._async_update_data()
        assert stale_decision.reason == "phase_sensor_stale"
        await asyncio.gather(*tasks)
        assert item.prices.snapshot["status"] == "ready"
        assert len(item.prices.snapshot["slots"]) == 2

        # A homeowner-declared fallback only keeps planned charging alive. It
        # caps both the plan and the final write while discharge stays disabled.
        item.config["phase_fallback_charge_power_w"] = 3000
        async def async_force_charge(*args):
            return "charge"

        controller.async_active_force_action = async_force_charge
        fallback_decision = await item._async_update_data()
        assert fallback_decision.action == "charge"
        assert fallback_decision.charge_power_w == 3000
        assert fallback_decision.discharge_power_w == 0
        assert fallback_decision.stale_phase_entities == ("sensor.l2",)
        assert fallback_decision.phase_fallback_charge_power_w == 3000
        assert item._guard_power_write(5000, "charge") == 3000

    asyncio.run(run())


def test_slow_or_failed_push_delivery_never_delays_or_changes_control(monkeypatch):
    cls = coordinator_class(monkeypatch)

    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        async def save(data):
            pass

        async def send(*args, **kwargs):
            started.set()
            await release.wait()
            raise RuntimeError("phone offline")

        hass = SimpleNamespace(
            services=SimpleNamespace(has_service=lambda *args: True, async_call=send),
            states=SimpleNamespace(get=lambda _: None), async_create_task=asyncio.create_task,
        )
        controller = SimpleNamespace(enabled=True, pause_reason=None)
        item = cls(hass, SimpleNamespace(data={}, options={}, entry_id="entry", title="Home"),
                   RuntimeStorage(SimpleNamespace(async_save=save), None), controller)
        await item.notifications.async_configure({
            "enabled": True, "targets": ["notify.mobile_app_phone"],
            "events": ["control_error"], "recovery": True, "language": "sv",
        })
        decision = Decision("blocked", 0, "control_write_failed")

        async def decide():
            return decision

        item._async_decide = decide
        assert await item._async_update_data() is decision
        await started.wait()
        assert not item.notification_task.done()
        assert await item._async_update_data() is decision
        release.set()
        await item.notification_task
        assert item.notifications.snapshot["last_error"]["error"] == "RuntimeError"
        assert controller.enabled is True

    asyncio.run(run())
