"""Forward real HA report timestamps, including unchanged zero readings."""

from __future__ import annotations

import re
from datetime import datetime
from math import isfinite


def report_time(state):
    """A repeated report is fresh even when the value has not changed."""
    return getattr(state, "last_reported", None) or getattr(state, "last_updated", None)


def is_load_power_measurement(entity_id: str, state) -> bool:
    """Only instantaneous power sensors can describe a thermostat's actual load."""
    if state is None or not entity_id.startswith("sensor."):
        return False
    text = f"{entity_id} {state.attributes.get('friendly_name', '')}".lower()
    return state.attributes.get("unit_of_measurement") in {"W", "kW", "MW"} and not re.search(
        r"average|genomsnitt|min(?:imum)?[_. ]|max(?:imum)?[_. ]|limit|accumulated|"
        r"recommended|recommendation|setpoint|börvärde|gridshift|forecast|grid_(?:charge|discharge)_power",
        text,
    )


def power_snapshot(hass, config: dict, now: datetime) -> dict:
    """Publish source readings, never a command or a fabricated report time."""
    fields = (
        "house_consumption_entity",
        "house_load_entity",
        "grid_power_entity",
        "grid_export_power_sensor",
        "battery_power_entity",
        "battery_charge_power_sensor",
        "battery_discharge_power_sensor",
        "solar_power_entities",
        "solar_power_entity",
        "ev_power_entities",
        "ev_power_entity",
    )
    entities = {}
    for field in fields:
        ids = config.get(field, [])
        ids = [ids] if isinstance(ids, str) else ids
        for entity_id in ids or []:
            state = hass.states.get(entity_id)
            stamp = report_time(state)
            if state is None or stamp is None:
                continue
            unit = state.attributes.get("unit_of_measurement")
            try:
                value = float(state.state)
            except (ValueError, TypeError):
                continue
            if unit not in {"W", "kW", "MW"} or not isfinite(value):
                continue
            entities[entity_id] = {
                "state": state.state,
                "unit_of_measurement": unit,
                "last_reported": stamp.isoformat(),
            }
    return {"captured_at": now.isoformat(), "entities": entities,
            "mapping": {key: config[key] for key in fields if config.get(key)}}
