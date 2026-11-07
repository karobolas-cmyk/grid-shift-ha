"""Normalize measurement conventions without depending on actuator setpoints."""

from __future__ import annotations

import re


def battery_power_inverted(entity_id: str, name: str = "", direction: str = "auto") -> bool:
    """Fronius SolarNet signed battery power is positive during discharge."""
    if direction != "auto":
        return direction == "positive_discharging"
    text = f"{entity_id} {name}".lower()
    return bool(re.search(r"(?:^|_)power_battery(?:_\d+)?$", entity_id.lower())) or (
        any(word in text for word in ("fronius", "solarnet"))
        and any(word in text for word in ("battery", "storage", "batteri", "byd"))
    )


def house_power_inverted(entity_id: str, direction: str = "auto") -> bool:
    """Fronius raw power_load is negative for consumption; consumed is positive."""
    if direction != "auto":
        return direction == "negative_consumption"
    return bool(re.search(r"(?:^|_)power_load(?:_\d+)?$", entity_id.lower()))


def house_input_is_grid(
    entity_id: str, name: str = "", source: str = "auto", legacy_grid: bool = False,
) -> bool:
    """Honor an explicit source role and otherwise recognize grid meter sensors."""
    if source != "auto":
        return source == "grid"
    text = f"{entity_id} {name}".lower()
    return legacy_grid or bool(re.search(
        r"power_grid|grid_power|grid_import|eln[aä]t[_. ]*import|nätimport|grid import|"
        r"(?:tibber|pulse).*(?:effekt|power)",
        text,
    ))


def grid_power_is_net(entity_id: str) -> bool:
    """A signed net meter already includes export; never subtract it twice."""
    return bool(re.search(r"(?:^|_)(?:power_grid|grid_power)(?:_\d+)?$", entity_id.lower()))


def house_from_grid_w(grid_w: float, solar_w: float, battery_w: float, export_w: float) -> float:
    """Return consumption including EV, excluding net battery charging."""
    return max(0.0, grid_w - export_w + solar_w - battery_w)


def observed_grid_charge_w(confirmed_w: int, battery_w: float, solar_w: float) -> int:
    """Conservatively bound the charge already included in measured phase load.

    A number entity confirms a setpoint, not delivered power. Subtracting all
    solar output also avoids treating PV battery charging as AC grid demand.
    """
    return int(max(0.0, min(confirmed_w, battery_w - max(0.0, solar_w))))
