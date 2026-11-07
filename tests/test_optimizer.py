"""Tests for planning and phase protection."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from custom_components.gridshift_battery.models import (
    EnergyForecast,
    OptimizerInput,
    PriceSlot,
)
from custom_components.gridshift_battery.optimizer import optimize

NOW = datetime(2026, 1, 15, 0, 10, tzinfo=UTC)


def scenario(
    *,
    prices: list[float],
    currents: tuple[float, float, float] = (5, 5, 5),
    soc: float = 20,
    previous: int = 0,
) -> OptimizerInput:
    slots = tuple(
        PriceSlot(
            NOW.replace(minute=0) + timedelta(hours=i),
            NOW.replace(minute=0) + timedelta(hours=i + 1),
            price,
        )
        for i, price in enumerate(prices)
    )
    forecasts = tuple(
        EnergyForecast(slot.start, slot.end, house_w=1000, solar_w=0, ev_w=0)
        for slot in slots
    )
    return OptimizerInput(
        now=NOW,
        soc_pct=soc,
        phase_currents_a=currents,
        price_slots=slots,
        forecasts=forecasts,
        battery_capacity_kwh=10,
        min_soc_pct=15,
        max_target_soc_pct=85,
        max_charge_power_w=5000,
        max_discharge_power_w=5000,
        main_fuse_a=20,
        phase_safety_margin_a=1,
        nominal_voltage_v=230,
        round_trip_efficiency=0.88,
        expensive_quantile=0.65,
        min_savings_percent=8,
        cycle_cost=0,
        previous_charge_power_w=previous,
    )


def test_charges_in_selected_cheap_slot() -> None:
    decision = optimize(scenario(prices=[0.3, 0.4, 0.8, 1.0, 1.8, 2.0, 1.5, 1.2]))
    assert decision.action == "charge"
    assert decision.charge_power_w == 2840
    assert decision.reason == "cheap_slot_and_capacity_available"
    assert decision.adaptive_target_soc_pct > 20


def test_most_loaded_phase_caps_balanced_charge() -> None:
    decision = optimize(
        scenario(prices=[0.3, 0.4, 0.8, 1.0, 1.8, 2.0], currents=(7, 18, 4))
    )
    assert decision.action == "charge"
    assert decision.phase_headroom_a == 1
    assert decision.charge_power_w == 690


def test_existing_charge_is_added_to_remaining_phase_headroom() -> None:
    decision = optimize(
        scenario(
            prices=[0.3, 0.4, 0.8, 1.0, 1.8, 2.0],
            currents=(13, 18, 12),
            previous=2000,
        )
    )
    assert decision.phase_limit_w == 2690
    assert decision.charge_power_w == 2690


def test_does_not_buy_at_expensive_price() -> None:
    decision = optimize(scenario(prices=[2.0, 0.3, 0.4, 0.8, 1.0, 1.8]))
    assert decision.action == "idle"
    assert decision.reason in {"price_spread_too_small", "cheaper_slots_available"}


def test_blocks_when_forecast_has_only_current_price() -> None:
    decision = optimize(scenario(prices=[0.3]))
    assert decision.action == "blocked"
    assert decision.reason == "price_forecast_unavailable"


def test_blocks_when_a_phase_has_no_margin() -> None:
    decision = optimize(
        scenario(prices=[0.3, 0.4, 1.0, 1.5, 2.0], currents=(5, 19.5, 5))
    )
    assert decision.action == "blocked"
    assert decision.reason == "phase_capacity_exhausted"


def test_swedish_winter_adds_heating_reserve() -> None:
    data = scenario(prices=[0.3, 0.4, 0.8, 1.0, 1.8, 2.0])
    decision = optimize(
        replace(
            data,
            winter_active=True,
            winter_solar_factor=0.2,
            outdoor_temperature_c=-5,
            heating_balance_temp_c=8,
            heating_reserve_kw_per_c=0.07,
        )
    )
    assert decision.winter_active is True
    assert decision.heating_reserve_kwh > 0
    assert decision.adaptive_target_soc_pct >= 40


def test_capacity_tariff_moves_charge_away_from_new_peak() -> None:
    data = scenario(prices=[0.3, 0.4, 0.8, 1.0, 1.8, 2.0])
    decision = optimize(
        replace(
            data,
            soc_pct=15,
            capacity_tariff_enabled=True,
            capacity_tariff_model="top3_average",
            capacity_tariff_rate_per_kw=100,
            capacity_tariff_reference_kw=4,
            capacity_tariff_start_hour=0,
            capacity_tariff_end_hour=1,
        )
    )

    assert decision.action == "idle"
    assert decision.reason == "capacity_tariff_peak_avoided"
    assert decision.capacity_tariff_active is True
    assert decision.capacity_tariff_incremental_peak_kw == 2
    assert decision.capacity_tariff_estimated_fee == 66.67


def test_capacity_tariff_is_opt_in() -> None:
    decision = optimize(scenario(prices=[0.3, 0.4, 0.8, 1.0, 1.8, 2.0]))
    assert decision.action == "charge"
    assert decision.capacity_tariff_enabled is False
    assert decision.capacity_tariff_estimated_fee == 0


def test_grid_discharge_requires_explicit_opt_in() -> None:
    decision = optimize(scenario(prices=[2.0, 0.3, 0.4, 0.8], soc=90))
    assert decision.action == "idle"
    assert decision.discharge_power_w == 0


def test_grid_discharge_uses_surplus_soc_when_profitable() -> None:
    data = replace(
        scenario(prices=[2.0, 0.3, 0.4, 0.8], soc=90),
        discharge_enabled=True,
    )
    decision = optimize(data)
    assert decision.action == "discharge"
    assert decision.reason == "export_price_profitable"
    assert decision.charge_power_w == 0
    assert decision.discharge_power_w == 5000


def test_grid_discharge_keeps_adaptive_soc_reserve() -> None:
    data = replace(
        scenario(prices=[2.0, 0.3, 0.4, 0.8], soc=20),
        discharge_enabled=True,
    )
    decision = optimize(data)
    assert decision.action != "discharge"
    assert decision.discharge_power_w == 0


def test_reserves_stored_energy_for_a_later_price_peak() -> None:
    decision = optimize(
        scenario(prices=[0.5, 0.4, 2.0, 2.2], soc=50)
    )
    assert decision.action == "hold"
    assert decision.reason == "battery_reserved_for_later_peak"
    assert decision.charge_power_w == 0
    assert decision.discharge_power_w == 0


def test_hold_requires_phase_margin_for_lost_battery_support() -> None:
    decision = optimize(
        scenario(prices=[0.5, 0.4, 2.0, 2.2], soc=50, currents=(18, 5, 5))
    )
    assert decision.action != "hold"

def test_manual_force_charge_overrides_expensive_price_but_keeps_phase_limit() -> None:
    data = replace(
        scenario(prices=[2.0, 0.3, 0.4, 0.8], currents=(7, 18, 4)),
        force_action="charge",
    )
    decision = optimize(data)

    assert decision.action == "charge"
    assert decision.reason == "manual_force_charge"
    assert decision.charge_power_w == 690


def test_manual_force_idle_overrides_a_selected_charge_slot() -> None:
    decision = optimize(
        replace(
            scenario(prices=[0.3, 0.4, 1.0, 1.8]),
            force_action="idle",
        )
    )

    assert decision.action == "idle"
    assert decision.reason == "manual_force_idle"


def test_manual_force_discharge_requires_opt_in_and_minimum_soc() -> None:
    blocked = optimize(
        replace(
            scenario(prices=[0.3, 0.4, 1.0, 1.8], soc=50),
            force_action="discharge",
        )
    )
    assert blocked.action == "blocked"
    assert blocked.reason == "grid_discharge_not_enabled"

    active = optimize(
        replace(
            scenario(prices=[0.3, 0.4, 1.0, 1.8], soc=50),
            force_action="discharge",
            discharge_enabled=True,
        )
    )
    assert active.action == "discharge"
    assert active.reason == "manual_force_discharge"
    assert active.discharge_power_w == 3500

