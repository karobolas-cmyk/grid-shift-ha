"""Small local model that learns the home's half-hour consumption profile."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from .models import EnergyForecast, PriceSlot

BIN_COUNT = 48
ALPHA = 0.15


@dataclass(slots=True)
class ProfileBin:
    """Exponentially weighted average for one half-hour of the day."""

    house_w: float = 0.0
    solar_w: float = 0.0
    ev_w: float = 0.0
    samples: int = 0

    def observe(self, house_w: float, solar_w: float, ev_w: float) -> None:
        """Update the bin while rejecting negative meter glitches."""
        house_w = max(0.0, house_w)
        solar_w = max(0.0, solar_w)
        ev_w = max(0.0, ev_w)
        if self.samples == 0:
            self.house_w, self.solar_w, self.ev_w = house_w, solar_w, ev_w
        else:
            self.house_w += ALPHA * (house_w - self.house_w)
            self.solar_w += ALPHA * (solar_w - self.solar_w)
            self.ev_w += ALPHA * (ev_w - self.ev_w)
        self.samples += 1


class ConsumptionModel:
    """Persistent, privacy-preserving consumption profile held inside HA."""

    def __init__(self, bins: list[ProfileBin] | None = None) -> None:
        self.bins = bins or [ProfileBin() for _ in range(BIN_COUNT)]

    @staticmethod
    def _bin_index(when: datetime) -> int:
        return when.hour * 2 + int(when.minute >= 30)

    def observe(
        self, when: datetime, house_w: float, solar_w: float = 0.0, ev_w: float = 0.0
    ) -> None:
        """Learn one current observation."""
        self.bins[self._bin_index(when)].observe(house_w, solar_w, ev_w)

    def _global_average(self, field: str) -> float:
        populated = [getattr(item, field) for item in self.bins if item.samples]
        return sum(populated) / len(populated) if populated else 0.0

    def forecast(self, slots: tuple[PriceSlot, ...]) -> tuple[EnergyForecast, ...]:
        """Forecast every slot from its local-time profile bin."""
        fallback = {
            name: self._global_average(name) for name in ("house_w", "solar_w", "ev_w")
        }
        result: list[EnergyForecast] = []
        for slot in slots:
            item = self.bins[self._bin_index(slot.start)]
            result.append(
                EnergyForecast(
                    start=slot.start,
                    end=slot.end,
                    house_w=item.house_w if item.samples else fallback["house_w"],
                    solar_w=item.solar_w if item.samples else fallback["solar_w"],
                    ev_w=item.ev_w if item.samples else fallback["ev_w"],
                )
            )
        return tuple(result)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the model for Home Assistant storage."""
        return {"bins": [asdict(item) for item in self.bins]}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ConsumptionModel:
        """Restore a model; malformed data falls back to a blank model."""
        if not data or not isinstance(data.get("bins"), list):
            return cls()
        try:
            bins = [ProfileBin(**item) for item in data["bins"]]
        except (TypeError, ValueError):
            return cls()
        return cls(bins if len(bins) == BIN_COUNT else None)
