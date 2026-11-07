# Changelog

All notable changes are documented here. The project follows semantic versioning.

## 0.12.2 - 2026-11-07

- Allow explicit manual charge, idle, and discharge actions when the automatic
  price forecast is unavailable.
- Keep state-of-charge limits, phase headroom, discharge opt-in, and the Fronius
  mode/write safety sequence active for every forced action.
- Add regression tests for force actions without price slots.

## 0.10.0 - 2026-11-07

- Added the narrowly scoped `gridshift_battery.apply_configuration` service used
  by the GridShift application after the user reviews entity discovery.
- Validate entity domains, existence, three unique phase sensors, Fronius mode
  options, supported fields, types, and safety ranges before saving anything.
- Disable active control before replacing a mapping, then reload the integration
  so recommendation sensors and dedicated control switches become available.
- Keep automatic battery control and discharge-to-grid disabled until their
  dedicated switches are explicitly enabled.

## 0.9.1 - 2026-11-07

- Replaced the entity-heavy Home Assistant setup wizard with a zero-input
  GridShift Bridge setup.
- New entries now load safely before GridShift has discovered and mapped any
  entities.
- Existing configured entries keep their current optimizer and battery-control
  behavior.
- Unconfigured bridge options direct the user back to GridShift instead of
  opening incomplete Home Assistant forms.

## 0.8.0 - 2026-11-06

- Added an energy-integration step for price, meter/phases, solar, and EV data.
- Added conservative entity discovery with suggested price, house-load, solar,
  charger, outdoor-temperature, and ordered L1/L2/L3 entities.
- Kept every discovered mapping editable before it is saved.
- Normalized empty, single, and multiple selector values and replaced generic
  Home Assistant flow failures with actionable translated errors plus log detail.

## 0.7.1 - 2026-11-06

- Completed HACS and Home Assistant manifest validation for the standalone
  integration repository.

## 0.7.0 - 2026-11-06

- Moved the Home Assistant integration to its own HACS repository:
  `karobolas-cmyk/grid-shift-ha`.
- Validated that the selected `fronius_modbus` control entity exposes `Auto`,
  `Charge from Grid`, and the optional `Discharge to Grid` mode before setup
  completes.
- Documented the mode-specific Fronius number entities: GridShift uses only
  `Grid Charge Power` and optional `Grid Discharge Power`; PV charge and
  discharge limit entities remain outside the current control contract.
- Added a configurable phase-sensor freshness limit (120 seconds by default).
  Stale, unavailable, or non-numeric phase data commands 0 W and returns a
  GridShift-owned charging or discharging mode to `Auto`.
- Added phase-data-age diagnostics and the stale phase entity IDs to the action
  sensor attributes.

## 0.6.0 - 2026-11-06

- Converted the hosted dashboard from a read-only preview into a live GridShift
  control surface.
- Added prominent top-level on/off controls for **Automatic battery control** and
  **Allow discharge to grid**, both reflecting the real Home Assistant switch
  state.
- Restricted the hosted relay to those two GridShift switches; arbitrary Home
  Assistant service calls remain blocked.
- Added confirmation before arming physical battery control or grid discharge,
  live state refresh, and safe-off behavior through the integration.
- Documented Home Assistant as the first provider adapter so future Homey and
  direct-inverter adapters can reuse the provider-neutral optimizer contracts.

## 0.5.0 - 2026-11-06

- Added a Fronius mode-settle-write state machine: select the storage mode, wait
  20 seconds by default, verify the matching number entity, then write power.
- Returning control now selects `Auto` without an unnecessary 0 W write.
- Rate updates reuse an already settled mode instead of selecting it again and
  resetting its mode-specific power number to zero.
- Added optional `Grid Discharge Power` mapping and `Discharge to Grid` mode.
- Added a separate persisted **Allow discharge to grid** switch; export cannot
  occur unless both it and automatic battery control are enabled.
- Added conservative export economics using efficiency, cycle cost, minimum
  saving, adaptive SoC reserve, maximum discharge power, and the three-phase
  main-fuse ceiling.
- Added recommendation sensors and tests for discharge power and for the exact
  Fronius mode → wait → power sequence.

## 0.4.0 - 2026-11-06

- Added Home Assistant EV charger profiles for Charge Amps, Easee, Zaptec,
  Wallbox, Tesla Wall Connector, go-e, myenergi Zappi, OCPP, Fronius Wattpilot,
  KEBA, OpenEVSE, Ohme, Peblar, Amina, and Sigenergy EVAC/EVDC.
- Added selection and summing of multiple EV charger power entities while
  preserving existing single-entity configurations.
- Added Sigenergy as a solar and energy-system source through Home Assistant,
  including local Modbus and cloud entity naming.
- Kept the hosted preview read-only; vendor-specific EV actuation is intentionally
  not enabled in this release.
- Added a rolling today-and-tomorrow price horizon with the current quarter
  centered in the chart.
- Added an explained quarter-hour timeline covering spot price, effective cost,
  solar, net grid load, weather, COP, and decision reason.
- Added click-to-toggle manual charging overrides while retaining SoC and live
  phase safety gates.
- Added thermostat freshness and 24-hour history checks before selection.
- Added house-construction profiles and optional indoor-temperature-history
  learning for thermal-mass estimates.
- Added multiple named Home Assistant homes with isolated browser-side settings
  and explicit per-home login and logout.
- Added automatic inverter and battery-system detection with a manual profile
  override, including Sigenergy, Sungrow, Huawei, Tesla Powerwall, sonnen,
  AlphaESS, FoxESS, and GivEnergy signatures.
- Rebranded the hosted experience as GridShift Battery Optimizer and replaced
  the decorative hero with a live solar, home, grid, battery, SoC, and EV flow.
- Added animated current-direction arrows for import, export, charge, discharge,
  generation, and consumption plus a battery icon filled to the current SoC.
- Prevented stationary storage charging/discharging sensors, such as Fronius
  `storage_charging_power`, from being auto-classified as EV chargers.
- Added Fronius storage charging power as a live battery-flow source and fixed
  dashboard overflow with shrinkable grids, container-aware metrics, and
  internal scrolling for wide timelines only.
- Added per-home main-fuse guidance: manual entry remains authoritative, with a
  confirmable suggestion when Home Assistant exposes an explicit fuse-size
  entity or attribute.

## 0.3.0 - 2026-11-06

- Added opt-in capacity-tariff planning with single-peak, top-three average, and
  top-five average models.
- Added configurable demand rate, billing-period reference peak, and active hours.
- Added tariff diagnostics to optimizer decisions.
- Added thermal planning in the hosted preview using weather, COP, thermal mass, and
  comfort limits.
- Added individual thermostat baselines, maximum deviations, price response 1–10,
  and direction-aware heat/cooling behavior.
- Added Home Assistant solar-provider selection and experimental direct Nord Pool
  day-ahead prices.
- Added multiple solar-entity summing and the official local Home Assistant
  Fronius integration as a secondary read source for additional inverters.
- Added generic COP profiles for air-to-air, air-to-water, borehole ground-source,
  horizontal ground-loop, and resistive heating.
- Added English-default and Swedish UI languages.
- Added a restricted server relay for Home Assistant OAuth and read-only entity,
  Tibber, and weather requests, avoiding browser CORS failures.

## 0.2.0 - 2026-11-06

- Added native price retrieval from Home Assistant's Tibber integration.
- Added 15-minute prices and 96 price intervals per day.
- Added Tibber Pulse power and L1/L2/L3 support.
- Added a Swedish winter model from 15 November through 15 March, with reduced
  solar credit and heating reserve.
- Added generic phase-current entities alongside Fronius Smart Meter.

## 0.1.0 - 2026-11-06

- Added the first local Home Assistant integration.
- Added adaptive SoC targets, price planning, and three-phase fuse protection.
- Added native control through `callifo/fronius_modbus` entities.
