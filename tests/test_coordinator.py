"""Exercise the real coordinator with small HA infrastructure stubs."""

import asyncio
import importlib
import sys
from datetime import UTC, datetime, timedelta
from types import ModuleType, SimpleNamespace

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
            SimpleNamespace(data=config, options={}),
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

    asyncio.run(run())
