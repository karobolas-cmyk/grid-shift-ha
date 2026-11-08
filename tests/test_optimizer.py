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
    # Cover the entire protected period, using only this interval's 50 minutes left.
    assert decision.charge_power_w == 4770
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


def reserve_gap_scenario(soc: float = 70) -> OptimizerInput:
    """A moderate-price gap before four dearer hours and a cheap refill."""
    return scenario(
        prices=[1.5, 1.6, 1.7, 2.8, 3.0, 3.2, 0.3, 0.3, 0.3, 0.3], soc=soc,
    )


def test_surplus_battery_supplies_home_in_a_gap_without_spending_later_reserve() -> None:
    decision = optimize(reserve_gap_scenario())
    assert decision.action == "idle"  # Native Auto/self-consumption, not grid export.
    assert decision.reason == "battery_surplus_available_for_home"
    assert decision.later_reserve_kwh == 4
    assert decision.available_for_home_kwh == 1.5
    assert decision.available_for_home_kwh > decision.home_demand_kwh
    assert decision.charge_power_w == decision.discharge_power_w == 0
    assert decision.plan["rows"][0]["projected_soc_end_pct"] > 15


def test_gap_is_reserved_when_the_battery_cannot_cover_the_gap_and_later_peaks() -> None:
    decision = optimize(reserve_gap_scenario(soc=60))
    assert decision.action == "hold"
    assert decision.later_reserve_kwh == 4
    assert decision.available_for_home_kwh == 0.5
    assert decision.available_for_home_kwh < decision.home_demand_kwh


def test_lower_real_consumption_releases_a_gap_on_the_next_measured_soc_pass() -> None:
    data = reserve_gap_scenario(soc=70)
    data = replace(
        data,
        price_slots=(replace(data.price_slots[0], price=3.1), *data.price_slots[1:]),
        forecasts=(replace(data.forecasts[0], house_w=2000), *data.forecasts[1:]),
    )
    projected_soc = optimize(data).plan["rows"][0]["projected_soc_end_pct"]
    at = data.price_slots[1].start
    expected = replace(data, now=at, soc_pct=projected_soc)
    actual = replace(data, now=at, soc_pct=68)
    assert optimize(expected).action == "hold"
    released = optimize(actual)
    assert released.action == "idle"
    assert released.reason == "battery_surplus_available_for_home"
    assert released.plan["actual_soc_pct"] == 68
    assert released.plan["generated_at"] == at.isoformat()


def test_higher_real_consumption_can_restore_reserve_between_quarter_boundaries() -> None:
    data = reserve_gap_scenario(soc=70)
    assert optimize(data).action == "idle"
    depleted = replace(data, now=data.now + timedelta(seconds=30), soc_pct=60)
    assert optimize(depleted).action == "hold"


def test_cheap_profitable_quarter_charges_instead_of_releasing_battery_to_home() -> None:
    data = reserve_gap_scenario(soc=20)
    first = replace(data.price_slots[0], price=0.2)
    decision = optimize(replace(data, price_slots=(first, *data.price_slots[1:])))
    assert decision.cheap_recharge_available is True
    assert decision.action == "charge"
    assert decision.charge_power_w > 0
    assert decision.charge_power_w <= decision.phase_limit_w


def test_cheap_quarter_preserves_energy_when_the_charging_target_is_already_met() -> None:
    data = reserve_gap_scenario(soc=80)
    first = replace(data.price_slots[0], price=0.2)
    decision = optimize(replace(data, price_slots=(first, *data.price_slots[1:])))
    assert decision.available_for_home_kwh > decision.home_demand_kwh
    assert decision.energy_needed_kwh == 0
    assert decision.cheap_recharge_available is True
    assert decision.action == "hold"


def test_a_refill_only_offsets_peaks_after_the_refill_and_honors_manual_off() -> None:
    data = reserve_gap_scenario(soc=70)
    # Put one cheap recharge before the peaks. It can cover the entire 4 kWh
    # reserve, but the manual Off choice must remove that capacity again.
    cheap = replace(data.price_slots[1], price=0.3)
    data = replace(data, price_slots=(data.price_slots[0], cheap, *data.price_slots[2:]))
    available = optimize(data)
    assert available.later_reserve_kwh == 0
    unavailable = optimize(replace(data, plan_overrides={cheap.start.isoformat(): "idle"}))
    assert unavailable.later_reserve_kwh == 4


def test_a_short_refill_cannot_release_energy_still_needed_for_later_peaks() -> None:
    data = reserve_gap_scenario(soc=60)
    cheap = replace(
        data.price_slots[1], price=0.3, end=data.price_slots[1].start + timedelta(minutes=15),
    )
    data = replace(data, price_slots=(data.price_slots[0], cheap, *data.price_slots[2:]))
    decision = optimize(data)
    assert decision.later_reserve_kwh == 2.9  # 4 kWh minus 5 kW × 0.25 h × 0.88.


def test_manual_quarter_choice_keeps_precedence_over_surplus_release() -> None:
    decision = optimize(replace(reserve_gap_scenario(), force_action="charge"))
    assert decision.action == "charge"
    assert decision.reason == "manual_force_charge"


def test_winter_heating_buffer_remains_reserved_before_releasing_surplus() -> None:
    data = replace(
        reserve_gap_scenario(), winter_active=True, outdoor_temperature_c=0,
        heating_balance_temp_c=8, heating_reserve_kw_per_c=0.0625,
    )
    decision = optimize(data)
    assert decision.later_reserve_kwh == 6
    assert decision.action == "hold"

def test_manual_force_charge_does_not_require_price_forecast() -> None:
    decision = optimize(
        replace(
            scenario(prices=[], currents=(7, 18, 4)),
            force_action="charge",
        )
    )

    assert decision.action == "charge"
    assert decision.reason == "manual_force_charge"
    assert decision.charge_power_w == 690


def test_manual_force_idle_does_not_require_price_forecast() -> None:
    decision = optimize(replace(scenario(prices=[]), force_action="idle"))

    assert decision.action == "idle"
    assert decision.reason == "manual_force_idle"


def test_manual_force_discharge_does_not_require_price_forecast() -> None:
    decision = optimize(
        replace(
            scenario(prices=[], soc=50),
            force_action="discharge",
            discharge_enabled=True,
        )
    )

    assert decision.action == "discharge"
    assert decision.reason == "manual_force_discharge"
    assert decision.discharge_power_w == 5000


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
    assert active.discharge_power_w == 4200


def test_missed_charge_replans_from_actual_soc_and_remaining_time() -> None:
    data = scenario(prices=[0.3, 0.4, 0.8, 1.0, 1.8, 2.0, 1.5, 1.2])
    initial = optimize(data)
    after_failure = optimize(replace(data, now=NOW + timedelta(hours=2)))

    assert initial.plan["rows"][0]["action"] == "charge"
    assert after_failure.action == "charge"
    assert after_failure.plan["actual_soc_pct"] == 20
    assert after_failure.plan["rows"][0]["projected_soc_start_pct"] == 20
    assert after_failure.plan["reserve_shortfall_kwh"] > 0
    assert after_failure.plan["stored_usable_energy_kwh"] == 0.5
    assert all(datetime.fromisoformat(row["end"]) > NOW + timedelta(hours=2)
               for row in after_failure.plan["rows"])
    assert set(initial.selected_slots) != set(after_failure.selected_slots)


def test_full_battery_and_undercharged_battery_get_different_future_plans() -> None:
    data = scenario(prices=[0.3, 0.4, 1.8, 2.0, 1.5, 1.2], soc=28)
    low = optimize(data)
    full = optimize(replace(data, soc_pct=85))

    assert low.selected_slots
    assert not full.selected_slots
    assert low.plan["energy_needed_kwh"] > full.plan["energy_needed_kwh"]
    assert low.plan["reserve_shortfall_kwh"] > full.plan["reserve_shortfall_kwh"]


def test_cheaper_price_after_peak_cannot_replace_charge_before_peak() -> None:
    data = scenario(prices=[0.4, 0.6, 2.0, 2.2, 0.1, 0.2, 0.3, 0.4], soc=15)
    decision = optimize(data)

    assert decision.action == "charge"
    first_peak = data.price_slots[2].start
    assert any(datetime.fromisoformat(start) < first_peak for start in decision.selected_slots)


def test_force_charge_keeps_rolling_plan_and_actual_reserve() -> None:
    data = replace(scenario(prices=[0.3, 0.4, 1.8, 2.0], soc=28), force_action="charge")
    decision = optimize(data)

    assert decision.reason == "manual_force_charge"
    assert len(decision.plan["rows"]) == 4
    assert decision.plan["rows"][0]["manual"] is True
    assert decision.plan["actual_soc_pct"] == 28
    assert decision.plan["target_soc_pct"] > 28


def test_future_manual_quarter_is_included_in_projection() -> None:
    data = scenario(prices=[0.3, 0.4, 1.8, 2.0], soc=15)
    second = data.price_slots[1].start.isoformat()
    decision = optimize(replace(data, plan_overrides={second: "idle"}))

    row = decision.plan["rows"][1]
    assert row["manual"] is True
    assert row["action"] == "idle"
    assert row["charge_power_w"] == 0


def test_overloaded_phase_reduces_existing_charge() -> None:
    data = scenario(prices=[0.3, 0.4, 1.8, 2.0], currents=(20, 5, 5), previous=2000)
    decision = optimize(data)
    assert decision.phase_limit_w == 1310


def large_battery_scenario() -> OptimizerInput:
    data = scenario(prices=[0.5] * 4 + [0.8] * 8 + [3] * 8 + [2] * 12 + [0.3] * 4, soc=15)
    start = NOW.replace(minute=0)
    slots = tuple(
        PriceSlot(start + timedelta(minutes=i * 15), start + timedelta(minutes=(i + 1) * 15),
                  slot.price)
        for i, slot in enumerate(data.price_slots)
    )
    forecasts = tuple(EnergyForecast(slot.start, slot.end, 4000, 0, 0) for slot in slots)
    return replace(
        data, now=start, price_slots=slots, forecasts=forecasts,
        battery_capacity_kwh=26, max_charge_power_w=10000, main_fuse_a=25,
        phase_currents_a=(1, 1, 1),
    )


def test_26_kwh_battery_cannot_refill_in_45_minutes_at_10_kw() -> None:
    decision = optimize(large_battery_scenario())
    plan = decision.plan
    assert plan["target_soc_pct"] == 85
    assert plan["energy_needed_kwh"] > 20
    assert plan["required_charge_minutes"] > 120
    assert sum(session["minutes"] for session in plan["charge_sessions"]) >= 120
    assert sum(session["grid_energy_kwh"] for session in plan["charge_sessions"]) > 20
    assert plan["unmet_target_kwh"] < 0.02
    assert max(row["projected_soc_end_pct"] for row in plan["rows"]) <= 85


def test_future_plan_uses_phase_capacity_and_reports_unreachable_target() -> None:
    data = replace(large_battery_scenario(), phase_currents_a=(7.4, 21.9, 5.2))
    decision = optimize(data)
    plan = decision.plan
    assert plan["planning_charge_power_w"] == 1440
    assert plan["required_charge_minutes"] > 800
    assert plan["projected_reserve_soc_pct"] < 35
    assert plan["unmet_target_kwh"] > 10
    assert all(row["charge_power_w"] <= 1440 for row in plan["rows"])


def test_reserve_covers_medium_prices_between_peaks_until_recharge() -> None:
    data = large_battery_scenario()
    prices = [0.5] * 12 + [3] * 4 + [1 + i / 100 for i in range(24)] + [3] * 4 + [0.3] * 4
    start = data.now
    slots = tuple(PriceSlot(
        start + timedelta(minutes=i * 15), start + timedelta(minutes=(i + 1) * 15), price
    ) for i, price in enumerate(prices))
    forecasts = tuple(EnergyForecast(slot.start, slot.end, 2000, 0, 0) for slot in slots)
    decision = optimize(replace(data, price_slots=slots, forecasts=forecasts))
    assert decision.plan["reserve_required_kwh"] == 16
    assert decision.plan["target_soc_pct"] > 75
    assert decision.plan["required_charge_minutes"] > 100


def test_short_cheap_window_only_credits_energy_it_can_deliver() -> None:
    data = large_battery_scenario()
    prices = [0.3] * 8 + [3] * 8 + [0.2] + [3] * 8 + [0.3]
    slots = tuple(PriceSlot(
        data.now + timedelta(minutes=i * 15),
        data.now + timedelta(minutes=(i + 1) * 15), price,
    ) for i, price in enumerate(prices))
    forecasts = tuple(EnergyForecast(slot.start, slot.end, 2000, 0, 0) for slot in slots)
    decision = optimize(replace(data, price_slots=slots, forecasts=forecasts))
    # Two 4 kWh demand periods, with only 2.2 kWh of feasible replenishment
    # between them: the first charge must reserve 5.8 kWh, not just 4 kWh.
    assert decision.plan["reserve_required_kwh"] == 5.8


