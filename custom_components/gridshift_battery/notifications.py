"""Opt-in anomaly notifications delivered locally by Home Assistant."""

from __future__ import annotations

import asyncio
import logging
import math
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime

from homeassistant.exceptions import HomeAssistantError

from .models import Decision

_LOGGER = logging.getLogger(__name__)
EVENTS = frozenset({"control_error", "missing_data", "reserve_shortfall"})
DEFAULT_SETTINGS = {
    "enabled": False, "targets": [], "events": sorted(EVENTS),
    "recovery": True, "language": "sv",
}
RETRY_SECONDS = 900
RECOVERY_SECONDS = 120


def validate_settings(hass, raw) -> dict:
    """Only allow selected mobile-app actions or existing notify entities."""
    if not isinstance(raw, dict) or set(raw) != set(DEFAULT_SETTINGS):
        raise HomeAssistantError("Expected enabled, targets, events, recovery and language")
    if not isinstance(raw["enabled"], bool) or not isinstance(raw["recovery"], bool):
        raise HomeAssistantError("enabled and recovery must be booleans")
    if raw["language"] not in ("sv", "en"):
        raise HomeAssistantError("Notification language must be sv or en")
    targets, events = raw["targets"], raw["events"]
    if not isinstance(targets, list) or len(targets) > 8 or any(
        not isinstance(target, str) or not re.fullmatch(r"notify\.[a-z0-9_]+", target)
        for target in targets
    ):
        raise HomeAssistantError("Select up to eight notification targets")
    if not isinstance(events, list) or len(events) > len(EVENTS) or any(
        not isinstance(event, str) or event not in EVENTS for event in events
    ):
        raise HomeAssistantError("Unsupported notification event")
    if raw["enabled"] and (not targets or not events):
        raise HomeAssistantError(
            "Select a phone and at least one event before enabling notifications"
        )
    if raw["enabled"]:
        for target in targets:
            if not target_available(hass, target):
                raise HomeAssistantError(f"Notification target is unavailable: {target}")
    return {**raw, "targets": sorted(set(targets)), "events": sorted(set(events))}


def target_available(hass, target: str) -> bool:
    """Recognize explicit recipients without enabling broadcast or arbitrary services."""
    action = target.removeprefix("notify.")
    return bool(
        action.startswith("mobile_app_") and hass.services.has_service("notify", action)
        or hass.states.get(target) is not None
        and hass.services.has_service("notify", "send_message")
    )


@dataclass(frozen=True)
class Anomaly:
    """A stable incident key; changing power values must not cause new alerts."""

    category: str
    title: tuple[str, str]
    message: tuple[str, str]
    delay: int = 0


def anomalies(
    decision: Decision | None, *, enabled: bool, pause_reason: str | None,
    thermal: dict, input_failed: bool = False,
) -> dict[str, Anomaly]:
    """Separate real faults from intentional Auto, hold and phase-limited charging."""
    result = {}
    reason = decision.reason if decision else ""
    if pause_reason or decision and (
        decision.control_error or reason in {
            "control_write_failed", "power_write_unconfirmed", "phase_sensor_stale_release_failed",
        }
    ):
        result["control"] = Anomaly("control_error",
            ("Battery control needs attention", "Batteristyrningen behöver åtgärdas"),
            ("An inverter command could not be verified. Check the GridShift event log and "
             "the Fronius mode/power mapping before re-enabling control.",
             "Ett kommando till växelriktaren kunde inte verifieras. Kontrollera GridShifts "
             "händelselogg och Fronius mappning av läge/effekt innan styrningen aktiveras igen."))
    if thermal.get("enabled") and (thermal.get("write_fault") or any(
        zone.get("status") in {"failed", "restore_unconfirmed", "readback_unconfirmed"}
        for zone in thermal.get("zones", [])
    )):
        result["climate"] = Anomaly("control_error",
            ("Climate control needs attention", "Klimatstyrningen behöver åtgärdas"),
            ("A thermostat command could not be confirmed. Check the climate status in GridShift.",
             "Ett termostatkommando kunde inte bekräftas. Kontrollera klimatstatus i GridShift."))
    if not enabled and not thermal.get("enabled"):
        return result
    if (
        input_failed or reason.startswith("phase_sensor_stale")
        or reason == "control_mode_unavailable"
    ):
        result["inputs"] = Anomaly("missing_data",
            ("Measurements are missing", "Mätvärden saknas"),
            ("GridShift cannot evaluate safely because required measurements are unavailable "
             "or stale. Check the phase and battery sensors in HA.",
             "GridShift kan inte utvärdera säkert eftersom nödvändiga mätvärden saknas eller "
             "är för gamla. Kontrollera fas- och batterisensorerna i HA."), 300)
    if reason == "price_forecast_unavailable":
        result["prices"] = Anomaly("missing_data",
            ("Price forecast is missing", "Prisprognosen saknas"),
            ("HA has no usable price forecast. Check GridShift's price source. Normal Auto "
             "self-consumption is not an execution error.",
             "HA saknar en användbar prisprognos. Kontrollera GridShifts priskälla. Normal "
             "egenförbrukning i Auto är inget styrfel."), 300)
    plan = decision.plan if decision else {}
    shortfall = plan.get("unmet_target_kwh")
    if enabled and plan.get("reserve_start") and isinstance(shortfall, (int, float)) \
            and math.isfinite(shortfall) and shortfall >= 1:
        result["reserve"] = Anomaly("reserve_shortfall",
            ("Charging plan may miss its target", "Laddplanen riskerar att missa målet"),
            (f"The rolling plan is short by {shortfall:.1f} kWh before the protected period. "
             "Check available charging time, battery level and phase headroom.",
             f"Den löpande planen saknar {shortfall:.1f} kWh före den skyddade perioden. "
             "Kontrollera tillgänglig laddtid, batterinivå och fasmarginal."), 600)
    return result


def _elapsed(now: datetime, stamp: str | None) -> float:
    try:
        return (now - datetime.fromisoformat(stamp or "")).total_seconds()
    except (ValueError, TypeError):
        return float("inf")


class NotificationManager:
    """Persist deduplication and per-recipient retries independently of battery commands."""

    def __init__(self, hass, storage, entry_id: str, home_name: str) -> None:
        self.hass, self.storage = hass, storage
        self.entry_id, self.home_name = entry_id, home_name
        self._lock = asyncio.Lock()
        self.state = storage.state.notification_state
        self.state.setdefault("incidents", {})
        self.state.setdefault("history", [])

    @property
    def settings(self) -> dict:
        return deepcopy({**DEFAULT_SETTINGS, **self.storage.state.notification_settings})

    @property
    def snapshot(self) -> dict:
        return {"settings": self.settings, "active": list(self.state["incidents"]),
                "last_sent": self.state.get("last_sent"),
                "last_error": self.state.get("last_error"),
                "history": deepcopy(self.state["history"][-20:])}

    async def async_configure(self, raw) -> None:
        settings = validate_settings(self.hass, raw)
        async with self._lock:
            if not settings["enabled"]:
                self.state["incidents"] = {}
            if not settings["enabled"] or (
                self.state.get("last_error") or {}
            ).get("target") not in settings["targets"]:
                self.state["last_error"] = None
            self.storage.state.notification_settings = settings
            await self.storage.async_save()

    async def _send(self, target: str, title: str, message: str, key: str) -> None:
        if not target_available(self.hass, target):
            raise HomeAssistantError("Notification target is unavailable")
        action = target.removeprefix("notify.")
        payload = {"title": f"GridShift · {self.home_name} · {title}", "message": message}
        if action.startswith("mobile_app_") and self.hass.services.has_service("notify", action):
            payload["data"] = {"tag": f"gridshift-{self.entry_id}-{key}"}
        else:
            action = "send_message"
            payload["entity_id"] = target
        async with asyncio.timeout(10):
            await self.hass.services.async_call("notify", action, payload, blocking=True)

    async def _deliver(self, record: dict, targets: list, key: str, title: str,
                       message: str, now: datetime, *, recovery: bool = False) -> None:
        delivered = record.setdefault("recovered" if recovery else "sent", [])
        attempts = record.setdefault("recovery_attempts" if recovery else "attempts", {})
        for target in targets:
            if target in delivered or _elapsed(now, attempts.get(target)) < RETRY_SECONDS:
                continue
            attempts[target] = now.isoformat()
            await self.storage.async_save()
            try:
                await self._send(target, title, message, key)
            except Exception as err:
                # Notification failures must never change an inverter decision.
                self.state["last_error"] = {"target": target, "error": type(err).__name__,
                                            "at": now.isoformat()}
                _LOGGER.warning("GridShift notification failed: %s", type(err).__name__)
                status = "failed"
            else:
                delivered.append(target)
                self.state["last_sent"] = now.isoformat()
                if (self.state.get("last_error") or {}).get("target") == target:
                    self.state["last_error"] = None
                status = "recovered" if recovery else "sent"
            self.state["history"].append({"at": now.isoformat(), "event": key,
                                          "target": target, "status": status})
            self.state["history"] = self.state["history"][-20:]
            await self.storage.async_save()

    async def async_evaluate(self, decision: Decision | None, now: datetime, *,
                             enabled: bool, pause_reason: str | None, thermal: dict,
                             input_failed: bool = False) -> None:
        """Alert once after persistence; recover only after two healthy minutes."""
        async with self._lock:
            settings = self.settings
            if not settings["enabled"]:
                return
            if not enabled and not pause_reason and not thermal.get("enabled"):
                # Deliberately switching control off is not a successful recovery.
                self.state["incidents"] = {}
                await self.storage.async_save()
                return
            index = 1 if settings["language"] == "sv" else 0
            active = {key: item for key, item in anomalies(
                decision, enabled=enabled, pause_reason=pause_reason,
                thermal=thermal, input_failed=input_failed,
            ).items() if item.category in settings["events"]}
            records = self.state["incidents"]
            for key, item in active.items():
                record = records.setdefault(key, {"first_seen": now.isoformat(),
                    "category": item.category, "title": item.title[index]})
                recovered = record.pop("recovered", [])
                if recovered:
                    # A recipient already told about recovery needs a new alert if it recurs.
                    record["sent"] = [t for t in record.get("sent", []) if t not in recovered]
                    for target in recovered:
                        record.get("attempts", {}).pop(target, None)
                    record.pop("recovery_attempts", None)
                    record["first_seen"] = now.isoformat()
                record.pop("clear_since", None)
                if _elapsed(now, record["first_seen"]) >= item.delay:
                    await self._deliver(record, settings["targets"], key,
                                        item.title[index], item.message[index], now)
            for key, record in list(records.items()):
                if key in active:
                    continue
                if record["category"] not in settings["events"]:
                    del records[key]
                    continue
                # Missing input data is not evidence that an earlier planning fault recovered.
                if input_failed or key == "reserve" and not (decision and decision.plan):
                    continue
                if not record.get("sent") or not settings["recovery"]:
                    del records[key]
                    continue
                record.setdefault("clear_since", now.isoformat())
                if _elapsed(now, record["clear_since"]) < RECOVERY_SECONDS:
                    continue
                targets = [t for t in record["sent"] if t in settings["targets"]]
                await self._deliver(record, targets, key,
                    ("Back to normal", "Tillbaka till normalt")[index],
                    (f"GridShift no longer reports: {record['title']}.",
                     f"GridShift rapporterar inte längre: {record['title']}.")[index],
                    now, recovery=True)
                if all(target in record.get("recovered", []) for target in targets):
                    del records[key]
            await self.storage.async_save()

    async def async_test(self, now: datetime) -> None:
        """Send only an explicitly requested test, including when alerts are disabled."""
        async with self._lock:
            settings = self.settings
            if not settings["targets"]:
                raise HomeAssistantError("Save a notification target first")
            if _elapsed(now, self.state.get("last_test")) < 60:
                raise HomeAssistantError("Wait one minute before sending another test")
            self.state["last_test"] = now.isoformat()
            await self.storage.async_save()
            index = 1 if settings["language"] == "sv" else 0
            record = {}
            await self._deliver(record, settings["targets"], "test",
                ("Test notification", "Testnotis")[index],
                ("This test was sent by Home Assistant. GridShift anomaly notifications "
                 "work even when the web page is closed.",
                 "Det här testet skickades av Home Assistant. GridShifts avvikelsenotiser "
                 "fungerar även när webbsidan är stängd.")[index], now)
            if len(record.get("sent", [])) != len(settings["targets"]):
                raise HomeAssistantError("HA could not send to all selected notification targets")
