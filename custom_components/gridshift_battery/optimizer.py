"""Deterministic battery optimizer with per-phase fuse protection."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from math import ceil

from .models import Decision, OptimizerInput, PriceSlot
from .price import current_slot


def _quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("values must not be empty")
    index = min(len(ordered) - 1, max(0, ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def _phase_limit(data: OptimizerInput) -> tuple[float, int]:
    # The meter current already contains an active charge command. Adding the
    # previous command back avoids a feedback loop that would otherwise reduce
    # the command on every pass.
    headroom = min(
        data.main_fuse_a - data.phase_safety_margin_a - max(0.0, current)
        for current in data.phase_currents_a
    )
    incremental_w = headroom * data.nominal_voltage_v * 3
    safe_total_w = int(max(0, data.previous_charge_power_w + incremental_w))
    return headroom, min(data.max_charge_power_w, safe_total_w)


def _balanced_discharge_limit(data: OptimizerInput) -> int:
    """Cap balanced three-phase export below the configured fuse rating."""
    per_phase_a = max(0.0, data.main_fuse_a - data.phase_safety_margin_a)
    fuse_limit_w = int(per_phase_a * data.nominal_voltage_v * 3)
    return min(data.max_discharge_power_w, fuse_limit_w)


def _forecast_for_slot(data: OptimizerInput, slot: PriceSlot) -> float:
    match = next(
        (item for item in data.forecasts if item.start == slot.start and item.end == slot.end),
        None,
    )
    if match is None:
        return 0.0
    solar_factor = data.winter_solar_factor if data.winter_active else 1.0
    return max(0.0, match.house_w - match.solar_w * solar_factor)


def _forecast_energy(data: OptimizerInput, field: str) -> float:
    return sum(
        getattr(item, field) * max(
            0.0, (item.end - max(item.start, data.now)).total_seconds() / 3600
        )
        / 1000
        for item in data.forecasts
    )


def _remaining_hours(data: OptimizerInput, slot: PriceSlot) -> float:
    """Never allocate energy to the elapsed part of the current interval."""
    return max(0.0, (slot.end - max(slot.start, data.now)).total_seconds() / 3600)


def _capacity_tariff_factor(model: str) -> float:
    """Return one new peak's share of the configured demand-charge model."""
    return {
        "single_peak": 1.0,
        "top3_average": 1 / 3,
        "top5_average": 1 / 5,
    }.get(model, 1.0)


def _capacity_tariff_window_active(data: OptimizerInput, slot: PriceSlot) -> bool:
    """Return whether a slot is inside the configured demand-charge window."""
    if not data.capacity_tariff_enabled:
        return False
    hour = slot.start.hour
    start = min(23, max(0, int(data.capacity_tariff_start_hour)))
    end = min(24, max(0, int(data.capacity_tariff_end_hour)))
    if start == end or (start == 0 and end == 24):
        return True
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _capacity_tariff_cost(
    data: OptimizerInput,
    slot: PriceSlot,
    charge_power_w: int | None = None,
) -> tuple[bool, float, float, float, float]:
    """Return tariff state, projected kW, incremental kW, fee, and fee per kWh."""
    active = _capacity_tariff_window_active(data, slot)
    power_w = data.max_charge_power_w if charge_power_w is None else max(0, charge_power_w)
    projected_kw = max(0.0, _forecast_for_slot(data, slot) / 1000 + power_w / 1000)
    incremental_kw = (
        max(0.0, projected_kw - data.capacity_tariff_reference_kw) if active else 0.0
    )
    fee = (
        incremental_kw
        * max(0.0, data.capacity_tariff_rate_per_kw)
        * _capacity_tariff_factor(data.capacity_tariff_model)
    )
    energy_kwh = power_w * _remaining_hours(data, slot) / 1000
    fee_per_kwh = fee / energy_kwh if energy_kwh > 0 else 0.0
    return active, projected_kw, incremental_kw, fee, fee_per_kwh


def _optimize_once(data: OptimizerInput) -> Decision:
    """Create a charge/idle decision for the active price slot.

    The planner first derives an adaptive target from learned demand during the
    expensive part of the horizon. It then allocates the required grid energy to
    the cheapest slots. The real-time command is capped by the most loaded phase.
    """
    future = tuple(slot for slot in data.price_slots if slot.end > data.now)
    active = current_slot(future, data.now)
    headroom, phase_limit_w = _phase_limit(data)
    force_base = {
        "current_price": active.price if active is not None else None,
        "phase_headroom_a": headroom,
        "phase_limit_w": phase_limit_w,
        "winter_active": data.winter_active,
        "capacity_tariff_enabled": data.capacity_tariff_enabled,
        "capacity_tariff_reference_kw": data.capacity_tariff_reference_kw,
    }

    # Manual actions are explicit real-time commands. They remain constrained by
    # SoC, phase capacity, discharge opt-in, and the controller safety sequence,
    # but must not depend on an automatic price forecast being available.
    if data.force_action == "idle":
        return Decision("idle", 0, "manual_force_idle", **force_base)
    if not 0 <= data.soc_pct <= 100:
        return Decision("blocked", 0, "invalid_state_of_charge", **force_base)
    if data.force_action == "charge":
        if data.soc_pct >= data.max_target_soc_pct:
            return Decision(
                "blocked", 0, "manual_force_charge_soc_limit", **force_base
            )
        command = min(data.max_charge_power_w, phase_limit_w) // 10 * 10
        if command < 100:
            return Decision("blocked", 0, "phase_capacity_exhausted", **force_base)
        return Decision("charge", command, "manual_force_charge", **force_base)
    if data.force_action == "discharge":
        if not data.discharge_enabled:
            return Decision(
                "blocked", 0, "grid_discharge_not_enabled", **force_base
            )
        discharge_energy_kwh = data.battery_capacity_kwh * max(
            0.0, data.soc_pct - data.min_soc_pct
        ) / 100
        duration_hours = _remaining_hours(data, active) if active is not None else 0.25
        energy_limit_w = int(
            discharge_energy_kwh * 1000 / max(duration_hours, 0.001)
        )
        command = min(_balanced_discharge_limit(data), energy_limit_w) // 10 * 10
        if command < 100:
            return Decision(
                "blocked", 0, "manual_force_discharge_soc_limit", **force_base
            )
        return Decision(
            "discharge",
            0,
            "manual_force_discharge",
            discharge_power_w=command,
            **force_base,
        )

    if active is None or len(future) < 2:
        return Decision("blocked", 0, "price_forecast_unavailable")
    if not 0 <= data.soc_pct <= 100:
        return Decision("blocked", 0, "invalid_state_of_charge")

    prices = [slot.price for slot in future]
    tariff_costs = {slot: _capacity_tariff_cost(data, slot) for slot in future}
    effective_prices = {
        slot: slot.price + tariff_costs[slot][4] for slot in future
    }
    expensive_cutoff = _quantile(prices, data.expensive_quantile)
    cheap_cutoff = _quantile(prices, 1 - data.expensive_quantile)
    expensive = [slot for slot in future if slot.price >= expensive_cutoff]
    expensive_load_kwh = sum(
        _forecast_for_slot(data, slot) * _remaining_hours(data, slot) / 1000
        for slot in expensive
    )
    heating_reserve_kwh = 0.0
    if data.winter_active and data.outdoor_temperature_c is not None:
        reserve_kw = max(
            0.0,
            data.heating_balance_temp_c - data.outdoor_temperature_c,
        ) * data.heating_reserve_kw_per_c
        heating_reserve_kwh = reserve_kw * sum(
            _remaining_hours(data, slot) for slot in expensive
        )
        expensive_load_kwh += heating_reserve_kwh

    usable_capacity_kwh = data.battery_capacity_kwh * max(
        0.0, data.max_target_soc_pct - data.min_soc_pct
    ) / 100
    desired_usable_kwh = min(usable_capacity_kwh, expensive_load_kwh)
    adaptive_target = min(
        data.max_target_soc_pct,
        data.min_soc_pct + 100 * desired_usable_kwh / data.battery_capacity_kwh,
    )
    # Before enough history is learned, still create a modest buffer instead of
    # disabling optimization entirely.
    adaptive_target = max(data.min_soc_pct + 10.0, adaptive_target)
    if data.winter_active:
        adaptive_target = max(adaptive_target, data.min_soc_pct + 25.0)
    adaptive_target = min(data.max_target_soc_pct, adaptive_target)

    battery_deficit_kwh = max(
        0.0, data.battery_capacity_kwh * (adaptive_target - data.soc_pct) / 100
    )
    grid_energy_needed_kwh = battery_deficit_kwh / max(0.01, data.round_trip_efficiency)
    expensive_reference = _quantile(prices, data.expensive_quantile)
    profitable_reference = expensive_reference * data.round_trip_efficiency - data.cycle_cost
    active_tariff = tariff_costs[active]
    active_effective_price = effective_prices[active]
    minimum_gain = active.price * data.min_savings_percent / 100

    percentile = sum(price <= active.price for price in prices) / len(prices) * 100

    base = {
        "current_price": active.price,
        "cheap_cutoff": cheap_cutoff,
        "expensive_reference": expensive_reference,
        "phase_headroom_a": headroom,
        "phase_limit_w": phase_limit_w,
        "adaptive_target_soc_pct": round(adaptive_target, 1),
        "energy_needed_kwh": round(grid_energy_needed_kwh, 3),
        "forecast_expensive_load_kwh": round(expensive_load_kwh, 3),
        "forecast_ev_energy_kwh": round(_forecast_energy(data, "ev_w"), 3),
        "forecast_solar_energy_kwh": round(_forecast_energy(data, "solar_w"), 3),
        "heating_reserve_kwh": round(heating_reserve_kwh, 3),
        "winter_active": data.winter_active,
        "price_percentile": round(percentile, 1),
        "capacity_tariff_enabled": data.capacity_tariff_enabled,
        "capacity_tariff_active": active_tariff[0],
        "capacity_tariff_reference_kw": data.capacity_tariff_reference_kw,
        "capacity_tariff_projected_kw": round(active_tariff[1], 3),
        "capacity_tariff_incremental_peak_kw": round(active_tariff[2], 3),
        "capacity_tariff_estimated_fee": round(active_tariff[3], 2),
    }

    later_expensive = tuple(
        slot
        for slot in future
        if slot.start >= active.end
        and slot.price >= expensive_cutoff
        and _forecast_for_slot(data, slot) > 100
    )
    stored_energy_kwh = data.battery_capacity_kwh * max(
        0.0, data.soc_pct - data.min_soc_pct
    ) / 100
    later_required_energy_kwh = sum(
        _forecast_for_slot(data, slot) * _remaining_hours(data, slot) / 1000
        for slot in later_expensive
    )
    later_peak_price = max(
        (slot.price for slot in later_expensive), default=active.price
    )
    hold_minimum_gain = max(0.01, abs(active.price)) * (
        data.min_savings_percent / 100
    )
    hold_phase_reserve_a = data.max_discharge_power_w / max(
        1.0, data.nominal_voltage_v * 3
    )
    should_hold = (
        stored_energy_kwh > 0.05
        and later_required_energy_kwh > 0.05
        and active.price < expensive_cutoff
        and later_peak_price - active.price >= hold_minimum_gain
        and headroom + data.previous_charge_power_w / max(
            1.0, data.nominal_voltage_v * 3
        ) >= hold_phase_reserve_a
    )

    def idle_or_hold(
        reason: str, selected_slots: tuple[str, ...] = ()
    ) -> Decision:
        if should_hold:
            return Decision(
                "hold",
                0,
                "battery_reserved_for_later_peak",
                selected_slots=selected_slots,
                **base,
            )
        return Decision("idle", 0, reason, selected_slots=selected_slots, **base)

    discharge_reserve_soc = max(data.min_soc_pct, adaptive_target)
    discharge_energy_kwh = data.battery_capacity_kwh * max(
        0.0, data.soc_pct - discharge_reserve_soc
    ) / 100
    replacement_cost = cheap_cutoff / max(0.01, data.round_trip_efficiency)
    replacement_cost += data.cycle_cost
    discharge_gain = active.price - replacement_cost
    discharge_minimum_gain = max(0.01, abs(cheap_cutoff)) * (
        data.min_savings_percent / 100
    )
    if (
        data.discharge_enabled
        and discharge_energy_kwh > 0.01
        and active.price >= expensive_cutoff
        and discharge_gain >= discharge_minimum_gain
    ):
        energy_limit_w = int(
            discharge_energy_kwh * 1000 / max(_remaining_hours(data, active), 0.001)
        )
        command = min(_balanced_discharge_limit(data), energy_limit_w)
        command = command // 10 * 10
        if command >= 100:
            return Decision(
                "discharge",
                0,
                "export_price_profitable",
                discharge_power_w=command,
                **base,
            )
    if grid_energy_needed_kwh <= 0.01:
        return idle_or_hold("adaptive_target_reached")
    if profitable_reference - active_effective_price < minimum_gain:
        reason = (
            "capacity_tariff_peak_avoided"
            if active_tariff[3] > 0
            else "price_spread_too_small"
        )
        return idle_or_hold(reason)
    if phase_limit_w < 100:
        return Decision("blocked", 0, "phase_capacity_exhausted", **base)

    # Allocate the energy need to the cheapest slots. Stable sorting by start
    # gives deterministic plans when several hours have the same price.
    selected: list[tuple[PriceSlot, int]] = []
    remaining = grid_energy_needed_kwh
    reserve_deadline = next((slot.start for slot in later_expensive), None)
    charging_window = tuple(
        slot for slot in future
        if reserve_deadline is None or slot.start < reserve_deadline
    )
    for slot in sorted(charging_window, key=lambda item: (effective_prices[item], item.start)):
        if remaining <= 0:
            break
        hours = _remaining_hours(data, slot)
        slot_capacity = data.max_charge_power_w * hours / 1000
        energy = min(remaining, slot_capacity)
        power = int(energy * 1000 / max(hours, 0.001))
        selected.append((slot, power))
        remaining -= energy

    active_plan = next((power for slot, power in selected if slot == active), 0)
    selected_starts = tuple(slot.start.isoformat() for slot, _ in selected)
    if active_plan <= 0:
        reason = (
            "capacity_tariff_peak_avoided"
            if active_tariff[3] > 0
            else "cheaper_slots_available"
        )
        return idle_or_hold(reason, selected_starts)

    # Fronius documents 10 W increments for Grid Charge Power.
    command = max(0, min(active_plan, phase_limit_w, data.max_charge_power_w))
    command = command // 10 * 10
    if command < 100:
        return Decision("blocked", 0, "phase_capacity_exhausted", **base)
    return Decision(
        "charge",
        command,
        "cheap_slot_and_capacity_available",
        selected_slots=selected_starts,
        **base,
    )


def optimize(data: OptimizerInput) -> Decision:
    """Rebuild a rolling plan from measured SoC, including during a force action."""
    current = _optimize_once(data)
    automatic = _optimize_once(replace(data, force_action=None))
    future = sorted(
        (slot for slot in data.price_slots if slot.end > data.now),
        key=lambda item: item.start,
    )[:192]
    overrides = {
        datetime.fromisoformat(start): action
        for start, action in data.plan_overrides.items()
    }
    soc = data.soc_pct
    rows = []
    baseline_currents = tuple(
        max(0.0, value - data.previous_charge_power_w / max(1, data.nominal_voltage_v * 3))
        for value in data.phase_currents_a
    )
    for index, slot in enumerate(future):
        at = max(data.now, slot.start)
        action_override = data.force_action if index == 0 else overrides.get(slot.start)
        projected = replace(
            data, now=at, soc_pct=soc, force_action=action_override,
            phase_currents_a=data.phase_currents_a if index == 0 else baseline_currents,
            previous_charge_power_w=data.previous_charge_power_w if index == 0 else 0,
        )
        decision = current if index == 0 else _optimize_once(projected)
        before = soc
        hours = _remaining_hours(projected, slot)
        if decision.action == "charge":
            soc += decision.charge_power_w * hours * data.round_trip_efficiency / (
                10 * data.battery_capacity_kwh
            )
        elif decision.action == "discharge":
            soc -= decision.discharge_power_w * hours / (10 * data.battery_capacity_kwh)
        elif decision.action == "idle":
            soc -= _forecast_for_slot(projected, slot) * hours / (
                10 * data.battery_capacity_kwh
            )
        if decision.action in {"idle", "discharge"}:
            soc = max(min(before, data.min_soc_pct), soc)
        soc = min(100.0, max(0.0, soc))
        forecast = next((item for item in data.forecasts if item.start == slot.start), None)
        solar_factor = data.winter_solar_factor if data.winter_active else 1.0
        rows.append({
            "start": slot.start.isoformat(), "end": slot.end.isoformat(),
            "action": decision.action, "reason": decision.reason,
            "charge_power_w": decision.charge_power_w,
            "discharge_power_w": decision.discharge_power_w,
            "projected_soc_start_pct": round(before, 2),
            "projected_soc_end_pct": round(soc, 2),
            "price": slot.price, "manual": action_override is not None,
            "forecast_house_w": forecast.house_w if forecast else 0,
            "forecast_ev_w": forecast.ev_w if forecast else 0,
            "forecast_solar_w": forecast.solar_w * solar_factor if forecast else 0,
        })
    plan = {
        "generated_at": data.now.isoformat(), "actual_soc_pct": data.soc_pct,
        "battery_capacity_kwh": data.battery_capacity_kwh,
        "target_soc_pct": automatic.adaptive_target_soc_pct,
        "energy_needed_kwh": automatic.energy_needed_kwh,
        "stored_usable_energy_kwh": round(
            data.battery_capacity_kwh * max(0, data.soc_pct - data.min_soc_pct) / 100, 3
        ),
        "reserve_required_kwh": automatic.forecast_expensive_load_kwh,
        "reserve_shortfall_kwh": round(max(
            0.0, automatic.forecast_expensive_load_kwh
            - data.battery_capacity_kwh * max(0, data.soc_pct - data.min_soc_pct) / 100,
        ), 3),
        "rows": rows,
    }
    selected = tuple(row["start"] for row in rows if row["action"] == "charge")
    if data.force_action is not None:
        current = replace(
            current,
            adaptive_target_soc_pct=automatic.adaptive_target_soc_pct,
            energy_needed_kwh=automatic.energy_needed_kwh,
            forecast_expensive_load_kwh=automatic.forecast_expensive_load_kwh,
            cheap_cutoff=automatic.cheap_cutoff,
            expensive_reference=automatic.expensive_reference,
            price_percentile=automatic.price_percentile,
        )
    return replace(current, selected_slots=selected, plan=plan)

