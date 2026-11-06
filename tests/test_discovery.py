from custom_components.gridshift_battery.discovery import (
    EntityCandidate,
    discover_energy_entities,
)


def test_discovers_common_home_energy_entities_in_phase_order() -> None:
    candidates = (
        EntityCandidate(
            "sensor.nordpool_kwh_se3_sek_1_10_025",
            integration="nordpool",
            name="Nord Pool price",
            unit="SEK/kWh",
        ),
        EntityCandidate(
            "sensor.smart_meter_real_power",
            integration="fronius_modbus",
            name="Real power consumed",
            device_class="power",
            unit="W",
        ),
        *tuple(
            EntityCandidate(
                f"sensor.smart_meter_ac_current_l{phase}",
                integration="fronius_modbus",
                name=f"AC current L{phase}",
                device_class="current",
                unit="A",
            )
            for phase in (1, 2, 3)
        ),
        EntityCandidate(
            "sensor.solarnet_generated_power",
            integration="fronius",
            name="Generated power",
            device_class="power",
            unit="W",
        ),
        EntityCandidate(
            "sensor.easee_charging_power",
            integration="easee",
            name="Charging power",
            device_class="power",
            unit="kW",
        ),
    )

    result = discover_energy_entities(
        candidates,
        price_provider="nordpool",
        meter_provider="fronius_modbus",
        solar_provider="fronius",
        ev_provider="easee",
    )

    assert result["price_entity"] == "sensor.nordpool_kwh_se3_sek_1_10_025"
    assert result["house_load_entity"] == "sensor.smart_meter_real_power"
    assert result["phase_current_entities"] == [
        "sensor.smart_meter_ac_current_l1",
        "sensor.smart_meter_ac_current_l2",
        "sensor.smart_meter_ac_current_l3",
    ]
    assert result["solar_power_entities"] == ["sensor.solarnet_generated_power"]
    assert result["ev_power_entities"] == ["sensor.easee_charging_power"]


def test_selected_provider_does_not_guess_from_another_integration() -> None:
    candidates = (
        EntityCandidate(
            "sensor.fronius_ac_current_l1",
            integration="fronius_modbus",
            name="AC current L1",
            device_class="current",
            unit="A",
        ),
    )
    result = discover_energy_entities(candidates, meter_provider="tibber")
    assert "phase_current_entities" not in result
