"""Config flow for GridShift Battery Optimizer."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    CONF_AUTO_MODE_OPTION,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_CAPACITY_TARIFF_ENABLED,
    CONF_CAPACITY_TARIFF_END_HOUR,
    CONF_CAPACITY_TARIFF_MODEL,
    CONF_CAPACITY_TARIFF_RATE_PER_KW,
    CONF_CAPACITY_TARIFF_REFERENCE_KW,
    CONF_CAPACITY_TARIFF_START_HOUR,
    CONF_CHARGE_MODE_OPTION,
    CONF_CHARGE_POWER_ENTITY,
    CONF_CONTROL_MODE_ENTITY,
    CONF_CYCLE_COST,
    CONF_DISCHARGE_MODE_OPTION,
    CONF_DISCHARGE_POWER_ENTITY,
    CONF_EV_PROVIDER,
    CONF_EV_POWER_ENTITIES,
    CONF_EXPENSIVE_QUANTILE,
    CONF_HEATING_BALANCE_TEMP_C,
    CONF_HEATING_RESERVE_KW_PER_C,
    CONF_HORIZON_HOURS,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_HOUSE_LOAD_INCLUDES_EV,
    CONF_INTERVAL_MINUTES,
    CONF_MAIN_FUSE_A,
    CONF_METER_PROVIDER,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MAX_DISCHARGE_POWER_W,
    CONF_MAX_TARGET_SOC_PCT,
    CONF_MIN_SAVINGS_PERCENT,
    CONF_MIN_SOC_PCT,
    CONF_MODE_SETTLE_SECONDS,
    CONF_NOMINAL_VOLTAGE_V,
    CONF_OUTDOOR_TEMPERATURE_ENTITY,
    CONF_PHASE_CURRENT_ENTITIES,
    CONF_PHASE_SAFETY_MARGIN_A,
    CONF_PHASE_SENSOR_MAX_AGE_SECONDS,
    CONF_PRICE_ENTITY,
    CONF_PRICE_PROVIDER,
    CONF_ROUND_TRIP_EFFICIENCY,
    CONF_SOC_ENTITY,
    CONF_SOLAR_POWER_ENTITIES,
    CONF_SOLAR_PROVIDER,
    CONF_SWEDISH_WINTER_MODE,
    CONF_WINTER_SOLAR_FACTOR,
    DEFAULTS,
    DOMAIN,
    FRONIUS_DOMAIN,
)
from .discovery import EntityCandidate, discover_energy_entities
from .fronius import missing_control_modes

_LOGGER = logging.getLogger(__name__)

PRICE_PROVIDERS = [
    {"value": "auto", "label": "Auto-detect"},
    {"value": "tibber", "label": "Tibber"},
    {"value": "nordpool", "label": "Nord Pool"},
    {"value": "manual", "label": "Manual / any integration"},
]
METER_PROVIDERS = [
    {"value": "auto", "label": "Auto-detect"},
    {"value": "fronius_modbus", "label": "Fronius Modbus"},
    {"value": "tibber", "label": "Tibber Pulse"},
    {"value": "sigenergy", "label": "Sigenergy"},
    {"value": "manual", "label": "Manual / any integration"},
]
SOLAR_PROVIDERS = [
    {"value": "auto", "label": "Auto-detect"},
    {"value": "fronius_modbus", "label": "Fronius Modbus"},
    {"value": "fronius", "label": "Fronius (official)"},
    {"value": "sigenergy", "label": "Sigenergy"},
    {"value": "solaredge", "label": "SolarEdge"},
    {"value": "enphase_envoy", "label": "Enphase Envoy"},
    {"value": "goodwe", "label": "GoodWe"},
    {"value": "sma", "label": "SMA"},
    {"value": "growatt", "label": "Growatt"},
    {"value": "solax", "label": "SolaX"},
    {"value": "victron", "label": "Victron"},
    {"value": "sungrow", "label": "Sungrow"},
    {"value": "manual", "label": "Manual / any integration"},
]
EV_PROVIDERS = [
    {"value": "auto", "label": "Auto-detect"},
    {"value": "chargeamps", "label": "Charge Amps"},
    {"value": "easee", "label": "Easee"},
    {"value": "zaptec", "label": "Zaptec"},
    {"value": "sigenergy", "label": "Sigenergy"},
    {"value": "wallbox", "label": "Wallbox"},
    {"value": "tesla", "label": "Tesla Wall Connector"},
    {"value": "goe", "label": "go-e"},
    {"value": "myenergi", "label": "myenergi Zappi"},
    {"value": "ocpp", "label": "OCPP"},
    {"value": "manual", "label": "Manual / any integration"},
]


def _entity(domain: str, integration: str | None = None, multiple: bool = False):
    entity_filter: dict[str, Any] = {"domain": domain}
    if integration:
        entity_filter["integration"] = integration
    return selector.EntitySelector(
        selector.EntitySelectorConfig(filter=entity_filter, multiple=multiple)
    )


def _number(
    minimum: float,
    maximum: float,
    step: float,
    unit: str | None = None,
    mode: selector.NumberSelectorMode = selector.NumberSelectorMode.BOX,
):
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum, max=maximum, step=step, unit_of_measurement=unit, mode=mode
        )
    )


def _provider_selector(options: list[dict[str, str]]):
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _as_entity_list(value: Any) -> list[str]:
    """Normalize Home Assistant's single and multiple selector values."""
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(entity_id) for entity_id in value if entity_id]


def _limits_schema(values: dict[str, Any]) -> vol.Schema:
    def suggested(key: str) -> dict[str, Any]:
        return {"suggested_value": values.get(key, DEFAULTS[key])}

    return vol.Schema(
        {
            vol.Required(
                CONF_BATTERY_CAPACITY_KWH,
                description=suggested(CONF_BATTERY_CAPACITY_KWH),
            ): _number(1, 200, 0.1, "kWh"),
            vol.Required(
                CONF_MIN_SOC_PCT, description=suggested(CONF_MIN_SOC_PCT)
            ): _number(0, 95, 1, "%"),
            vol.Required(
                CONF_MAX_TARGET_SOC_PCT,
                description=suggested(CONF_MAX_TARGET_SOC_PCT),
            ): _number(5, 100, 1, "%"),
            vol.Required(
                CONF_MAX_CHARGE_POWER_W,
                description=suggested(CONF_MAX_CHARGE_POWER_W),
            ): _number(100, 50000, 10, "W"),
            vol.Required(
                CONF_MAX_DISCHARGE_POWER_W,
                description=suggested(CONF_MAX_DISCHARGE_POWER_W),
            ): _number(100, 50000, 10, "W"),
            vol.Required(
                CONF_MAIN_FUSE_A, description=suggested(CONF_MAIN_FUSE_A)
            ): _number(6, 100, 0.5, "A"),
            vol.Required(
                CONF_PHASE_SAFETY_MARGIN_A,
                description=suggested(CONF_PHASE_SAFETY_MARGIN_A),
            ): _number(0, 10, 0.5, "A"),
            vol.Required(
                CONF_PHASE_SENSOR_MAX_AGE_SECONDS,
                description=suggested(CONF_PHASE_SENSOR_MAX_AGE_SECONDS),
            ): _number(15, 900, 15, "s"),
            vol.Required(
                CONF_NOMINAL_VOLTAGE_V,
                description=suggested(CONF_NOMINAL_VOLTAGE_V),
            ): _number(200, 250, 1, "V"),
            vol.Required(
                CONF_INTERVAL_MINUTES, description=suggested(CONF_INTERVAL_MINUTES)
            ): _number(1, 60, 1, "min"),
            vol.Required(
                CONF_HORIZON_HOURS, description=suggested(CONF_HORIZON_HOURS)
            ): _number(6, 48, 1, "h"),
            vol.Required(
                CONF_EXPENSIVE_QUANTILE,
                description=suggested(CONF_EXPENSIVE_QUANTILE),
            ): _number(0.5, 0.9, 0.05),
            vol.Required(
                CONF_MIN_SAVINGS_PERCENT,
                description=suggested(CONF_MIN_SAVINGS_PERCENT),
            ): _number(0, 100, 1, "%"),
            vol.Required(
                CONF_ROUND_TRIP_EFFICIENCY,
                description=suggested(CONF_ROUND_TRIP_EFFICIENCY),
            ): _number(0.5, 1.0, 0.01),
            vol.Required(
                CONF_CYCLE_COST, description=suggested(CONF_CYCLE_COST)
            ): _number(0, 10, 0.01),
            vol.Required(
                CONF_CAPACITY_TARIFF_ENABLED,
                description=suggested(CONF_CAPACITY_TARIFF_ENABLED),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_CAPACITY_TARIFF_MODEL,
                description=suggested(CONF_CAPACITY_TARIFF_MODEL),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["single_peak", "top3_average", "top5_average"],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_CAPACITY_TARIFF_RATE_PER_KW,
                description=suggested(CONF_CAPACITY_TARIFF_RATE_PER_KW),
            ): _number(0, 1000, 1, "SEK/kW"),
            vol.Required(
                CONF_CAPACITY_TARIFF_REFERENCE_KW,
                description=suggested(CONF_CAPACITY_TARIFF_REFERENCE_KW),
            ): _number(0, 100, 0.1, "kW"),
            vol.Required(
                CONF_CAPACITY_TARIFF_START_HOUR,
                description=suggested(CONF_CAPACITY_TARIFF_START_HOUR),
            ): _number(0, 23, 1, "h"),
            vol.Required(
                CONF_CAPACITY_TARIFF_END_HOUR,
                description=suggested(CONF_CAPACITY_TARIFF_END_HOUR),
            ): _number(0, 24, 1, "h"),
            vol.Required(
                CONF_HOUSE_LOAD_INCLUDES_EV,
                description=suggested(CONF_HOUSE_LOAD_INCLUDES_EV),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_SWEDISH_WINTER_MODE,
                description=suggested(CONF_SWEDISH_WINTER_MODE),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_WINTER_SOLAR_FACTOR,
                description=suggested(CONF_WINTER_SOLAR_FACTOR),
            ): _number(0, 1, 0.05),
            vol.Required(
                CONF_HEATING_BALANCE_TEMP_C,
                description=suggested(CONF_HEATING_BALANCE_TEMP_C),
            ): _number(-10, 20, 0.5, "°C"),
            vol.Required(
                CONF_HEATING_RESERVE_KW_PER_C,
                description=suggested(CONF_HEATING_RESERVE_KW_PER_C),
            ): _number(0, 1, 0.01, "kW/°C"),
            vol.Required(
                CONF_CHARGE_MODE_OPTION, description=suggested(CONF_CHARGE_MODE_OPTION)
            ): selector.TextSelector(),
            vol.Required(
                CONF_DISCHARGE_MODE_OPTION,
                description=suggested(CONF_DISCHARGE_MODE_OPTION),
            ): selector.TextSelector(),
            vol.Required(
                CONF_AUTO_MODE_OPTION, description=suggested(CONF_AUTO_MODE_OPTION)
            ): selector.TextSelector(),
            vol.Required(
                CONF_MODE_SETTLE_SECONDS,
                description=suggested(CONF_MODE_SETTLE_SECONDS),
            ): _number(0, 60, 1, "s"),
        }
    )


def _validate_limits(data: dict[str, Any]) -> str | None:
    if data[CONF_MAX_TARGET_SOC_PCT] <= data[CONF_MIN_SOC_PCT]:
        return "target_not_above_minimum"
    if data[CONF_PHASE_SAFETY_MARGIN_A] >= data[CONF_MAIN_FUSE_A]:
        return "margin_exceeds_fuse"
    return None


def _validate_control_modes(
    mode_state: Any,
    values: dict[str, Any],
    *,
    discharge_configured: bool,
) -> str | None:
    """Ensure the selected Fronius control entity exposes GridShift's modes."""
    options = mode_state.attributes.get("options", []) if mode_state else []
    missing = missing_control_modes(
        options,
        auto_mode=values[CONF_AUTO_MODE_OPTION],
        charge_mode=values[CONF_CHARGE_MODE_OPTION],
        discharge_mode=(
            values[CONF_DISCHARGE_MODE_OPTION] if discharge_configured else None
        ),
    )
    return "required_control_modes_missing" if missing else None


class GridShiftConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle setup through the Home Assistant UI."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Select the Fronius entities used for battery control."""
        errors: dict[str, str] = {}
        if user_input is not None:
            mode_state = self.hass.states.get(user_input[CONF_CONTROL_MODE_ENTITY])
            power_state = self.hass.states.get(user_input[CONF_CHARGE_POWER_ENTITY])
            discharge_entity = user_input.get(CONF_DISCHARGE_POWER_ENTITY)
            discharge_state = (
                self.hass.states.get(discharge_entity) if discharge_entity else None
            )
            soc_state = self.hass.states.get(user_input[CONF_SOC_ENTITY])
            if (
                mode_state is None
                or power_state is None
                or soc_state is None
                or (discharge_entity and discharge_state is None)
            ):
                errors["base"] = "entity_unavailable"
            else:
                self._data.update(user_input)
                return await self.async_step_energy_sources()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SOC_ENTITY): _entity("sensor", FRONIUS_DOMAIN),
                    vol.Required(CONF_CONTROL_MODE_ENTITY): _entity("select", FRONIUS_DOMAIN),
                    vol.Required(CONF_CHARGE_POWER_ENTITY): _entity("number", FRONIUS_DOMAIN),
                    vol.Optional(CONF_DISCHARGE_POWER_ENTITY): _entity(
                        "number", FRONIUS_DOMAIN
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_energy_sources(
        self, user_input: dict[str, Any] | None = None
    ):
        """Choose integrations before GridShift proposes entity mappings."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_energy()

        return self.async_show_form(
            step_id="energy_sources",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PRICE_PROVIDER, default="auto"): (
                        _provider_selector(PRICE_PROVIDERS)
                    ),
                    vol.Required(CONF_METER_PROVIDER, default="auto"): (
                        _provider_selector(METER_PROVIDERS)
                    ),
                    vol.Required(CONF_SOLAR_PROVIDER, default="auto"): (
                        _provider_selector(SOLAR_PROVIDERS)
                    ),
                    vol.Required(CONF_EV_PROVIDER, default="auto"): (
                        _provider_selector(EV_PROVIDERS)
                    ),
                }
            ),
        )

    def _energy_candidates(self) -> tuple[EntityCandidate, ...]:
        """Read minimal discovery metadata from Home Assistant's entity registry."""
        registry = er.async_get(self.hass)
        candidates: list[EntityCandidate] = []
        for state in self.hass.states.async_all():
            if not state.entity_id.startswith("sensor."):
                continue
            entry = registry.async_get(state.entity_id)
            if entry is not None and entry.disabled_by is not None:
                continue
            candidates.append(
                EntityCandidate(
                    entity_id=state.entity_id,
                    integration=entry.platform if entry is not None else "",
                    name=str(state.attributes.get("friendly_name", "")),
                    device_class=str(state.attributes.get("device_class", "")),
                    unit=str(state.attributes.get("unit_of_measurement", "")),
                )
            )
        return tuple(candidates)

    def _energy_suggestions(self) -> dict[str, str | list[str]]:
        return discover_energy_entities(
            self._energy_candidates(),
            price_provider=self._data.get(CONF_PRICE_PROVIDER, "auto"),
            meter_provider=self._data.get(CONF_METER_PROVIDER, "auto"),
            solar_provider=self._data.get(CONF_SOLAR_PROVIDER, "auto"),
            ev_provider=self._data.get(CONF_EV_PROVIDER, "auto"),
        )

    @staticmethod
    def _suggested(
        suggestions: dict[str, str | list[str]], key: str
    ) -> dict[str, Any] | None:
        value = suggestions.get(key)
        return {"suggested_value": value} if value else None

    async def async_step_energy(self, user_input: dict[str, Any] | None = None):
        """Confirm discovered price, load, production, EV, and phase entities."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                phase_entities = _as_entity_list(
                    user_input.get(CONF_PHASE_CURRENT_ENTITIES)
                )
                user_input[CONF_PHASE_CURRENT_ENTITIES] = phase_entities
                for key in (CONF_EV_POWER_ENTITIES, CONF_SOLAR_POWER_ENTITIES):
                    selected = _as_entity_list(user_input.get(key))
                    if selected:
                        user_input[key] = selected
                    else:
                        user_input.pop(key, None)

                required = [
                    user_input.get(CONF_PRICE_ENTITY),
                    user_input.get(CONF_HOUSE_LOAD_ENTITY),
                    *phase_entities,
                ]
                optional = [
                    user_input.get(CONF_OUTDOOR_TEMPERATURE_ENTITY),
                    *_as_entity_list(user_input.get(CONF_EV_POWER_ENTITIES)),
                    *_as_entity_list(user_input.get(CONF_SOLAR_POWER_ENTITIES)),
                ]
                if len(phase_entities) != 3 or len(set(phase_entities)) != 3:
                    errors["base"] = "three_phases_required"
                elif any(not entity_id for entity_id in required) or any(
                    self.hass.states.get(entity_id) is None
                    for entity_id in (*required, *(item for item in optional if item))
                ):
                    errors["base"] = "entity_unavailable"
                else:
                    self._data.update(user_input)
                    try:
                        return await self.async_step_limits()
                    except Exception:
                        _LOGGER.exception("Could not open GridShift safety settings")
                        errors["base"] = "configuration_step_failed"
            except (KeyError, TypeError, ValueError):
                _LOGGER.exception("Invalid GridShift energy entity selection")
                errors["base"] = "invalid_energy_selection"

        suggestions = self._energy_suggestions()

        return self.async_show_form(
            step_id="energy",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PRICE_ENTITY,
                        description=self._suggested(suggestions, CONF_PRICE_ENTITY),
                    ): _entity("sensor"),
                    vol.Required(
                        CONF_HOUSE_LOAD_ENTITY,
                        description=self._suggested(
                            suggestions, CONF_HOUSE_LOAD_ENTITY
                        ),
                    ): _entity("sensor"),
                    vol.Optional(
                        CONF_EV_POWER_ENTITIES,
                        description=self._suggested(
                            suggestions, CONF_EV_POWER_ENTITIES
                        ),
                    ): _entity(
                        "sensor", multiple=True
                    ),
                    vol.Optional(
                        CONF_SOLAR_POWER_ENTITIES,
                        description=self._suggested(
                            suggestions, CONF_SOLAR_POWER_ENTITIES
                        ),
                    ): _entity(
                        "sensor", multiple=True
                    ),
                    vol.Optional(
                        CONF_OUTDOOR_TEMPERATURE_ENTITY,
                        description=self._suggested(
                            suggestions, CONF_OUTDOOR_TEMPERATURE_ENTITY
                        ),
                    ): _entity("sensor"),
                    vol.Required(
                        CONF_PHASE_CURRENT_ENTITIES,
                        description=self._suggested(
                            suggestions, CONF_PHASE_CURRENT_ENTITIES
                        ),
                    ): _entity(
                        "sensor", multiple=True
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_limits(self, user_input: dict[str, Any] | None = None):
        """Configure physical and economic guardrails."""
        errors: dict[str, str] = {}
        if user_input is not None:
            error = _validate_limits(user_input)
            if error is None:
                error = _validate_control_modes(
                    self.hass.states.get(self._data[CONF_CONTROL_MODE_ENTITY]),
                    user_input,
                    discharge_configured=bool(
                        self._data.get(CONF_DISCHARGE_POWER_ENTITY)
                    ),
                )
            if error:
                errors["base"] = error
            else:
                self._data.update(user_input)
                await self.async_set_unique_id(self._data[CONF_CONTROL_MODE_ENTITY])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="GridShift Battery", data=self._data)
        return self.async_show_form(
            step_id="limits", data_schema=_limits_schema(DEFAULTS), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return GridShiftOptionsFlow(config_entry)


class GridShiftOptionsFlow(config_entries.OptionsFlow):
    """Edit limits without recreating the integration."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            error = _validate_limits(user_input)
            if error is None:
                error = _validate_control_modes(
                    self.hass.states.get(
                        self._entry.data[CONF_CONTROL_MODE_ENTITY]
                    ),
                    user_input,
                    discharge_configured=bool(
                        self._entry.data.get(CONF_DISCHARGE_POWER_ENTITY)
                    ),
                )
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(title="", data=user_input)
        values = {**self._entry.data, **self._entry.options}
        return self.async_show_form(
            step_id="init", data_schema=_limits_schema(values), errors=errors
        )
