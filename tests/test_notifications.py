"""Notification policy, real HA payloads, persistence and delivery isolation."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.gridshift_battery.controller import RuntimeStorage
from custom_components.gridshift_battery.models import Decision
from custom_components.gridshift_battery.notifications import (
    DEFAULT_SETTINGS,
    NotificationManager,
    anomalies,
    validate_settings,
)

NOW = datetime(2026, 11, 7, 19, tzinfo=UTC)
PHONE = "notify.mobile_app_phone"
SECOND = "notify.mobile_app_second"
HEALTHY = Decision("idle", 0, "not_a_cheap_interval")
FAULT = Decision("blocked", 0, "control_write_failed")
PRICES = Decision("blocked", 0, "price_forecast_unavailable")


class Services:
    def __init__(self):
        self.calls, self.fail = [], set()

    def has_service(self, domain, action):
        return domain == "notify" and action in {"mobile_app_phone", "mobile_app_second",
                                                  "send_message"}

    async def async_call(self, domain, action, data, **kwargs):
        self.calls.append((domain, action, deepcopy(data), kwargs))
        if action in self.fail:
            raise HomeAssistantError("offline")


def manager(raw=None):
    saved = []

    async def save(data):
        saved.append(deepcopy(data))

    hass = SimpleNamespace(services=Services(), states=SimpleNamespace(
        get=lambda entity_id: object() if entity_id == "notify.phone_entity" else None,
    ))
    storage = RuntimeStorage(SimpleNamespace(async_save=save), raw)
    return NotificationManager(hass, storage, "home-id", "Test home"), saved


def settings(**updates):
    return {**deepcopy(DEFAULT_SETTINGS), "enabled": True, "targets": [PHONE], **updates}


async def evaluate(item, decision, offset=0, **kwargs):
    await item.async_evaluate(decision, NOW + timedelta(seconds=offset),
        enabled=kwargs.pop("enabled", True), pause_reason=kwargs.pop("pause_reason", None),
        thermal=kwargs.pop("thermal", {}), **kwargs)


def test_settings_are_opt_in_and_never_allow_broadcast_or_arbitrary_services():
    item, _ = manager()
    assert item.settings["enabled"] is False
    for raw in (settings(targets=["notify.notify"]), settings(targets=["light.turn_on"]),
                settings(events=["all"]), settings(targets=[]), settings(enabled="yes"),
                settings(targets=[PHONE] * 9), settings(message="arbitrary message")):
        with pytest.raises(HomeAssistantError):
            validate_settings(item.hass, raw)
    assert validate_settings(item.hass, settings(targets=[PHONE, PHONE]))["targets"] == [PHONE]
    assert validate_settings(item.hass, settings(targets=["notify.phone_entity"]))


def test_intentional_auto_hold_and_phase_limit_are_not_faults():
    for decision in (HEALTHY, Decision("hold", 0, "battery_reserved_for_later_peak"),
                     Decision("charge", 920, "phase_capacity_limited")):
        assert not anomalies(decision, enabled=True, pause_reason=None, thermal={})
    # A current reserve deficit is fine if the rolling plan can fill it in time.
    plan = {"reserve_shortfall_kwh": 17, "unmet_target_kwh": 0, "reserve_start": NOW.isoformat()}
    assert not anomalies(Decision("charge", 10000, "cheap_interval", plan=plan),
                         enabled=True, pause_reason=None, thermal={})
    assert "control" in anomalies(Decision("blocked", 0, "phase_sensor_stale_release_failed"),
                                  enabled=True, pause_reason=None, thermal={})
    assert "climate" in anomalies(HEALTHY, enabled=False, pause_reason=None,
                                  thermal={"enabled": True, "write_fault": True})


def test_disabled_alerts_and_unarmed_battery_send_nothing():
    async def run():
        item, _ = manager()
        await evaluate(item, FAULT)
        assert not item.hass.services.calls
        await item.async_configure(settings())
        await evaluate(item, PRICES, enabled=False)
        assert not item.hass.services.calls
    asyncio.run(run())


def test_fault_sends_once_per_recipient_and_survives_restart():
    async def run():
        item, saved = manager()
        await item.async_configure(settings(targets=[PHONE, SECOND]))
        await evaluate(item, FAULT, enabled=False,
                       pause_reason="control_paused_after_write_failure")
        await evaluate(item, FAULT, 900)
        assert len(item.hass.services.calls) == 2
        call = item.hass.services.calls[0]
        assert call[0:2] == ("notify", "mobile_app_phone")
        assert call[2]["data"]["tag"] == "gridshift-home-id-control"
        assert "Test home" in call[2]["title"]
        assert "mappning" in call[2]["message"]
        restarted, _ = manager(saved[-1])
        await evaluate(restarted, FAULT, 1800)
        assert not restarted.hass.services.calls
    asyncio.run(run())


def test_transient_price_failure_is_silent_then_recovers_once():
    async def run():
        item, _ = manager()
        await item.async_configure(settings())
        await evaluate(item, PRICES)
        await evaluate(item, HEALTHY, 60)
        assert not item.hass.services.calls
        await evaluate(item, PRICES, 120)
        await evaluate(item, PRICES, 419)
        assert not item.hass.services.calls
        await evaluate(item, PRICES, 420)
        assert len(item.hass.services.calls) == 1
        await evaluate(item, HEALTHY, 450)
        await evaluate(item, PRICES, 470)
        await evaluate(item, HEALTHY, 500)
        await evaluate(item, HEALTHY, 619)
        assert len(item.hass.services.calls) == 1
        await evaluate(item, HEALTHY, 620)
        await evaluate(item, HEALTHY, 900)
        assert len(item.hass.services.calls) == 2
        assert "Tillbaka till normalt" in item.hass.services.calls[-1][2]["title"]
    asyncio.run(run())


def test_partial_delivery_retries_only_failed_phone_after_fifteen_minutes():
    async def run():
        item, _ = manager()
        await item.async_configure(settings(targets=[PHONE, SECOND]))
        item.hass.services.fail.add("mobile_app_phone")
        await evaluate(item, FAULT)
        assert len(item.hass.services.calls) == 2
        assert item.snapshot["last_error"]["target"] == PHONE
        await evaluate(item, FAULT, 899)
        assert len(item.hass.services.calls) == 2
        item.hass.services.fail.clear()
        await evaluate(item, FAULT, 900)
        assert len(item.hass.services.calls) == 3
        assert item.hass.services.calls[-1][1] == "mobile_app_phone"
        assert item.snapshot["last_error"] is None
    asyncio.run(run())


def test_modern_notify_entity_and_user_requested_test_work_while_disabled():
    async def run():
        item, _ = manager()
        await item.async_configure(settings(enabled=False, targets=["notify.phone_entity"]))
        await item.async_test(NOW)
        call = item.hass.services.calls[0]
        assert call[1] == "send_message"
        assert call[2]["entity_id"] == "notify.phone_entity"
        assert "data" not in call[2]
        with pytest.raises(HomeAssistantError, match="one minute"):
            await item.async_test(NOW + timedelta(seconds=59))
        assert len(item.hass.services.calls) == 1
        assert item.settings["enabled"] is False
    asyncio.run(run())


def test_plan_shortfall_requires_ten_minutes_and_one_kwh_of_projected_deficit():
    async def run():
        item, _ = manager()
        await item.async_configure(settings(events=["reserve_shortfall"], language="en"))
        decision = Decision("charge", 920, "cheap_interval", plan={
            "reserve_start": (NOW + timedelta(hours=3)).isoformat(), "unmet_target_kwh": 5.2,
        })
        await evaluate(item, decision)
        await evaluate(item, decision, 599)
        assert not item.hass.services.calls
        await evaluate(item, decision, 600)
        assert "5.2 kWh" in item.hass.services.calls[0][2]["message"]
        await evaluate(item, PRICES, 700)
        await evaluate(item, PRICES, 900)
        assert len(item.hass.services.calls) == 1, "Missing forecast cannot prove reserve recovery"
    asyncio.run(run())


def test_switching_control_off_does_not_claim_a_successful_recovery():
    async def run():
        item, _ = manager()
        await item.async_configure(settings())
        await evaluate(item, PRICES)
        await evaluate(item, PRICES, 300)
        await evaluate(item, HEALTHY, 400, enabled=False)
        await evaluate(item, HEALTHY, 600, enabled=False)
        assert len(item.hass.services.calls) == 1
    asyncio.run(run())


def test_missing_measurements_use_the_same_five_minute_debounce():
    async def run():
        item, _ = manager()
        await item.async_configure(settings())
        await evaluate(item, None, input_failed=True)
        await evaluate(item, None, 299, input_failed=True)
        assert not item.hass.services.calls
        await evaluate(item, None, 300, input_failed=True)
        assert "Mätvärden saknas" in item.hass.services.calls[0][2]["title"]
    asyncio.run(run())


def test_recurrence_realerts_a_phone_that_already_received_partial_recovery():
    async def run():
        item, _ = manager()
        await item.async_configure(settings(targets=[PHONE, SECOND]))
        await evaluate(item, FAULT)
        item.hass.services.fail.add("mobile_app_second")
        await evaluate(item, HEALTHY, 60)
        await evaluate(item, HEALTHY, 180)
        assert len(item.hass.services.calls) == 4
        await evaluate(item, FAULT, 181)
        assert len(item.hass.services.calls) == 5
        assert item.hass.services.calls[-1][1] == "mobile_app_phone"
        assert "Batteristyrningen" in item.hass.services.calls[-1][2]["title"]
    asyncio.run(run())
