from custom_components.gridshift_battery.fronius import missing_control_modes


def test_grid_modes_are_supported() -> None:
    assert not missing_control_modes(
        [
            "Auto",
            "PV Charge Limit",
            "Discharge Limit",
            "Charge from Grid",
            "Block Discharging",
            "Discharge to Grid",
        ],
        auto_mode="Auto",
        charge_mode="Charge from Grid",
        block_discharge_mode="Block Discharging",
        discharge_mode="Discharge to Grid",
    )


def test_missing_grid_discharge_mode_is_reported() -> None:
    assert missing_control_modes(
        ["Auto", "Charge from Grid"],
        auto_mode="Auto",
        charge_mode="Charge from Grid",
        discharge_mode="Discharge to Grid",
    ) == ("Discharge to Grid",)


def test_discharge_mode_is_optional_without_a_power_entity() -> None:
    assert not missing_control_modes(
        ["Auto", "Charge from Grid"],
        auto_mode="Auto",
        charge_mode="Charge from Grid",
    )


def test_missing_block_discharge_mode_is_reported() -> None:
    assert missing_control_modes(
        ["Auto", "Charge from Grid"],
        auto_mode="Auto",
        charge_mode="Charge from Grid",
        block_discharge_mode="Block Discharging",
    ) == ("Block Discharging",)
