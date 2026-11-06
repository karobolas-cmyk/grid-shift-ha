"""Pure Home Assistant entity discovery for the GridShift setup flow."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EntityCandidate:
    """A small, Home Assistant independent entity description."""

    entity_id: str
    integration: str = ""
    name: str = ""
    device_class: str = ""
    unit: str = ""

    @property
    def search_text(self) -> str:
        """Return normalized text used by deterministic discovery rules."""
        return " ".join(
            (self.entity_id, self.integration, self.name, self.device_class, self.unit)
        ).lower().replace("_", " ").replace("-", " ")


PROVIDER_ALIASES = {
    "nordpool": ("nordpool", "nord_pool"),
    "sigenergy": ("sigenergy", "sigenergy2mqtt", "sigenergy_local"),
    "chargeamps": ("chargeamps", "charge_amplitude"),
    "goe": ("goecharger", "go_e"),
    "myenergi": ("myenergi", "zappi"),
}


def _provider_matches(candidate: EntityCandidate, provider: str) -> bool:
    if provider in ("", "auto", "manual"):
        return True
    integration = candidate.integration.lower()
    aliases = PROVIDER_ALIASES.get(provider, (provider,))
    return any(alias in integration for alias in aliases)


def _with_provider(
    candidates: tuple[EntityCandidate, ...], provider: str
) -> tuple[EntityCandidate, ...]:
    return tuple(item for item in candidates if _provider_matches(item, provider))


def _is_power(candidate: EntityCandidate) -> bool:
    return candidate.device_class.lower() == "power" or candidate.unit.lower() in {
        "w",
        "kw",
        "mw",
    }


def _is_current(candidate: EntityCandidate) -> bool:
    return candidate.device_class.lower() == "current" or candidate.unit.lower() in {
        "a",
        "ma",
    }


def _best(
    candidates: tuple[EntityCandidate, ...], score_fn
) -> str | None:
    ranked = sorted(
        ((score_fn(item), item.entity_id) for item in candidates),
        key=lambda item: (-item[0], item[1]),
    )
    return ranked[0][1] if ranked and ranked[0][0] > 0 else None


def _token_score(text: str, positive: tuple[str, ...], negative: tuple[str, ...]) -> int:
    return sum(12 for token in positive if token in text) - sum(
        20 for token in negative if token in text
    )


def _phase_score(candidate: EntityCandidate, phase: int) -> int:
    if not _is_current(candidate):
        return -100
    text = candidate.search_text
    phase_match = re.search(rf"\b(?:l|phase)\s*0?{phase}\b", text)
    return 40 + (80 if phase_match else -100)


def discover_energy_entities(
    candidates: tuple[EntityCandidate, ...],
    *,
    price_provider: str = "auto",
    meter_provider: str = "auto",
    solar_provider: str = "auto",
    ev_provider: str = "auto",
) -> dict[str, str | list[str]]:
    """Suggest a conservative entity mapping for user confirmation."""
    sensors = tuple(item for item in candidates if item.entity_id.startswith("sensor."))
    price_candidates = _with_provider(sensors, price_provider)
    meter_candidates = _with_provider(sensors, meter_provider)
    solar_candidates = _with_provider(sensors, solar_provider)
    ev_candidates = _with_provider(sensors, ev_provider)

    result: dict[str, str | list[str]] = {}
    price = _best(
        price_candidates,
        lambda item: _token_score(
            item.search_text,
            ("electricity price", "energy price", "current price", "nordpool", "price"),
            ("forecast temperature",),
        )
        + (35 if "/kwh" in item.unit.lower() else 0),
    )
    if price:
        result["price_entity"] = price

    house = _best(
        meter_candidates,
        lambda item: (30 if _is_power(item) else -100)
        + _token_score(
            item.search_text,
            ("house", "home", "load", "consumption", "consumed", "real power"),
            ("solar", "pv", "battery", "storage", "charger", " ev ", "export"),
        ),
    )
    if house:
        result["house_load_entity"] = house

    phases = [
        _best(meter_candidates, lambda item, phase=phase: _phase_score(item, phase))
        for phase in (1, 2, 3)
    ]
    if all(phases) and len(set(phases)) == 3:
        result["phase_current_entities"] = phases  # type: ignore[assignment]

    solar = _best(
        solar_candidates,
        lambda item: (30 if _is_power(item) else -100)
        + _token_score(
            item.search_text,
            ("solar", " pv ", "photovoltaic", "generated power", "generation power"),
            ("energy", "total", "battery", "storage", "limit"),
        ),
    )
    if solar:
        result["solar_power_entities"] = [solar]

    ev = _best(
        ev_candidates,
        lambda item: (30 if _is_power(item) else -100)
        + _token_score(
            item.search_text,
            ("charger power", "charging power", "evse", "wallbox", "charge amp"),
            ("battery", "storage", "solar", "limit"),
        ),
    )
    if ev:
        result["ev_power_entities"] = [ev]

    outdoor = _best(
        sensors,
        lambda item: (30 if item.device_class.lower() == "temperature" else 0)
        + _token_score(
            item.search_text,
            ("outdoor", "outside", "external temperature", "ute temperatur"),
            ("indoor", "inside", "battery", "inverter"),
        ),
    )
    if outdoor:
        result["outdoor_temperature_entity"] = outdoor
    return result
