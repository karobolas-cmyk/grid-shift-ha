"""Tests for the local consumption profile."""

from datetime import UTC, datetime, timedelta

from custom_components.gridshift_battery.learning import ConsumptionModel
from custom_components.gridshift_battery.models import PriceSlot


def test_unknown_bins_use_learned_global_average() -> None:
    model = ConsumptionModel()
    now = datetime(2026, 1, 1, 8, 10, tzinfo=UTC)
    model.observe(now, house_w=1200, solar_w=200, ev_w=300)
    future = PriceSlot(now + timedelta(hours=6), now + timedelta(hours=7), 1.0)
    forecast = model.forecast((future,))[0]
    assert forecast.house_w == 1200
    assert forecast.solar_w == 200
    assert forecast.ev_w == 300


def test_model_round_trip() -> None:
    model = ConsumptionModel()
    now = datetime(2026, 1, 1, 8, 10, tzinfo=UTC)
    model.observe(now, house_w=1200, solar_w=200, ev_w=300)
    restored = ConsumptionModel.from_dict(model.to_dict())
    assert restored.to_dict() == model.to_dict()
