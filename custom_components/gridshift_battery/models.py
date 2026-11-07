"""Pure data models used by the optimizer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class PriceSlot:
    """A price valid for a time interval."""

    start: datetime
    end: datetime
    price: float

    @property
    def duration_hours(self) -> float:
        """Return slot duration in hours."""
        return max(0.0, (self.end - self.start).total_seconds() / 3600)


@dataclass(frozen=True, slots=True)
class EnergyForecast:
    """Learned average powers for one future price slot."""

    start: datetime
    end: datetime
    house_w: float
    solar_w: float
    ev_w: float

    @property
    def net_load_w(self) -> float:
        """Return forecast demand after solar production."""
        return max(0.0, self.house_w - self.solar_w)


@dataclass(frozen=True, slots=True)
class OptimizerInput:
    """All inputs needed for a deterministic planning pass."""

    now: datetime
    soc_pct: float
    phase_currents_a: tuple[float, float, float]
    price_slots: tuple[PriceSlot, ...]
    forecasts: tuple[EnergyForecast, ...]
    battery_capacity_kwh: float
    min_soc_pct: float
    max_target_soc_pct: float
    max_charge_power_w: int
    max_discharge_power_w: int
    main_fuse_a: float
    phase_safety_margin_a: float
    nominal_voltage_v: float
    round_trip_efficiency: float
    expensive_quantile: float
    min_savings_percent: float
    cycle_cost: float
    previous_charge_power_w: int = 0
    discharge_enabled: bool = False
    force_action: Literal["charge", "discharge", "idle"] | None = None
    winter_active: bool = False
    winter_solar_factor: float = 1.0
    outdoor_temperature_c: float | None = None
    heating_balance_temp_c: float = 8.0
    heating_reserve_kw_per_c: float = 0.0
    capacity_tariff_enabled: bool = False
    capacity_tariff_model: str = "top3_average"
    capacity_tariff_rate_per_kw: float = 0.0
    capacity_tariff_reference_kw: float = 0.0
    capacity_tariff_start_hour: int = 0
    capacity_tariff_end_hour: int = 24


Action = Literal["charge", "hold", "discharge", "idle", "blocked"]


@dataclass(frozen=True, slots=True)
class Decision:
    """One optimizer decision, suitable for entities and diagnostics."""

    action: Action
    charge_power_w: int
    reason: str
    discharge_power_w: int = 0
    current_price: float | None = None
    cheap_cutoff: float | None = None
    expensive_reference: float | None = None
    phase_headroom_a: float | None = None
    phase_limit_w: int = 0
    phase_data_age_seconds: float | None = None
    stale_phase_entities: tuple[str, ...] = field(default_factory=tuple)
    adaptive_target_soc_pct: float | None = None
    energy_needed_kwh: float = 0.0
    forecast_expensive_load_kwh: float = 0.0
    forecast_ev_energy_kwh: float = 0.0
    forecast_solar_energy_kwh: float = 0.0
    heating_reserve_kwh: float = 0.0
    winter_active: bool = False
    price_percentile: float | None = None
    capacity_tariff_enabled: bool = False
    capacity_tariff_active: bool = False
    capacity_tariff_reference_kw: float = 0.0
    capacity_tariff_projected_kw: float = 0.0
    capacity_tariff_incremental_peak_kw: float = 0.0
    capacity_tariff_estimated_fee: float = 0.0
    selected_slots: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        """Return a Home Assistant friendly mapping."""
        return asdict(self)
