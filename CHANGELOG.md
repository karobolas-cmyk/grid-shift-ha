# Changelog

All notable changes are documented here. The project follows semantic versioning.

## 0.14.1 - 2026-11-08

- Preserve saved battery, grid-export and climate opt-ins across upgrades,
  integration reloads, HA restarts, and settings changes for the same actuators.
  Reapplying an unchanged configuration is a no-op.
- Treat a missing/unknown/unavailable inverter mode during startup as a wait,
  rather than a manual mode override that permanently disables control.
  Reevaluate immediately when the mode entity becomes available.
- Quiesce in-flight commands before reload without persisting an Off state or
  latching an update-related write failure. Drain old updates before loading a
  new worker; resume the existing worker if platform unload fails.
- Preserve intentional Off and latched fault states. A genuine manual mode
  override still disarms control. Newly mapped actuators and thermostat scopes
  require explicit arming; existing settings and source changes keep their opt-ins.
- Add upgrade/reload regression tests against the real configuration service and
  unload handlers, including startup mode restoration and an in-flight command.

## 0.14.0 - 2026-11-07

- Add opt-in anomaly push notifications through selected HA Companion App
  actions or notify entities. Settings and per-recipient deduplication persist
  in Home Assistant and work with the dashboard closed.
- Notify on unconfirmed battery/thermostat commands immediately, missing
  prices or required measurements after five minutes, and a projected reserve
  deficit of at least 1 kWh sustained for ten minutes. Normal Auto, reserve
  holding and phase-limited charging do not count as control errors.
- Send one message per ongoing incident; optionally notify after two healthy
  minutes. Retry only failed recipients after fifteen minutes. Delivery failure
  cannot block or change battery control.
- Add separate set_notifications and test_notifications services so notification
  settings do not restart the bridge or disable active battery/climate control.
- Include notification settings, recent delivery attempts and errors in the
  action sensor's diagnostics. Tests never imply phone delivery confirmation.

## 0.13.1 - 2026-11-07

- Add an explicit `house_consumption_entity`, independent of grid import and
  battery power. Prefer the fresh total measurement in planning and learning;
  a saved grid-source setting cannot reinterpret it as grid import.
- Recognize Fronius `solarnet_power_load_consumed` as total house consumption
  and Swedish `solarnet_elnat_import` as a grid-meter input.
- Expose the saved measurement mapping and bridge capabilities to the dashboard.
- Refresh prices independently of phase-safety failures. Stopping physical
  control for stale phases no longer leaves price diagnostics uninitialized.
- Explain current-price-only entities, missing published coverage and service
  availability in the native price snapshot.
- Dashboard compatibility keeps published legacy/entity prices available when
  a native snapshot is missing or empty, without extending expired coverage.

## 0.13.0 - 2026-11-07

- Persist the chosen price provider in Home Assistant. Support native direct
  Nord Pool, the official Nord Pool service, Tibber, and mapped price attributes.
- Expose the exact published price series to the dashboard. Cache valid prices
  across restarts and transport failures; never extend prices beyond coverage.
- Keep multiple Tibber homes separate and require selection when ambiguous.
- Forward actual HA report times for power measurements, so unchanged 0 W
  readings remain usable without refreshing stopped sensors artificially.
- Use report times for phase freshness and refresh native diagnostics every
  30 seconds, including when meter readings are unchanged.
- Add separately armed Automatic climate control for explicitly mapped zones,
  bounded baseline/deviation, heating/cooling direction and native price/COP
  ranking. Do not switch HVAC modes or turn inactive equipment on.
- Stagger thermostat writes, confirm target readback, respect manual changes,
  and reserve phase capacity before increasing thermal demand. Preserve battery
  guards and explicit grid-export opt-in.
- Restore targets still owned by GridShift on disable/reconfiguration/unload;
  a readback or restoration failure remains visible in diagnostics.

## 0.12.7 - 2026-11-07

- Normalize Fronius SolarNet battery power: negative means charging, positive
  means discharging. Add explicit per-home polarity overrides in the bridge.
- Recognize a grid meter selected as the house input, normalize Fronius raw
  house consumption, and avoid subtracting export twice from signed grid power.
- Relearn household consumption after measurement mappings or conventions
  change, so battery charging is not retained as historical household demand.
- Budget future charging with estimated phase capacity, rather than assuming
  the inverter's nominal maximum is always available.
- When battery measurements are mapped, bound existing grid charging by actual
  battery power after solar, so an undelivered setpoint does not add phase capacity.
- Reserve energy for demand through the next cheap recharge opportunity,
  including moderately priced intervals between expensive peaks.
- Expose charging sessions with duration, grid energy and projected SoC, plus
  required charge time and a warning when phase limits prevent reaching target.
- Preserve existing phase guards, explicit export opt-in, and latched failures.

## 0.12.6 - 2026-11-07

- Rebuild the remaining plan from measured SoC on input updates and at quarter
  boundaries. Missed charging windows never contribute stored energy.
- Expose the rolling plan, projected SoC per interval, measured stored reserve,
  and reserve shortfall on the action sensor, including during forced charging.
- Allocate charging before the upcoming expensive period instead of relying on
  cheaper energy that only becomes available after that period.
- Use only the remaining duration of the active interval for energy allocation.
- Support deriving house consumption from instantaneous grid import, grid
  export, solar and measured battery power. Never use a charge setpoint as a
  battery measurement or count battery charging as household demand.
- Continue producing a read-only plan while a prior actuator failure is latched;
  the existing explicit re-enable requirement and bounded write retries remain.
- Reduce an existing balanced charge when a phase is already above its margin.

## 0.12.5 - 2026-11-07

- Reject charge/discharge entity mix-ups, including previously saved mappings
  where Grid discharge power was selected as Grid charge power.

- Stop the Auto/Charge loop by latching a failed actuator sequence. Automatic
  control stays paused across input updates and restarts until explicitly re-enabled.
- Keep a mode with a fresh reported 0 W setpoint after a failed power write.
  Unknown or non-zero power still causes one safe return to Auto before pausing.
- Retry transient rate-write and readback failures up to three times with five
  seconds between attempts, without selecting the storage mode again.
- Allow up to 60 seconds for a mode-specific number entity to become available,
  and honor the settle period if the mode was changed shortly before a retry.
- Add attempt history, mode timing, and the mapped number's limits to diagnostics.

## 0.12.4 - 2026-11-07

- Confirm every charge/discharge setpoint with a fresh matching Home Assistant
  report after the number service succeeds. A restored non-zero Fronius rate,
  including an old rate equal to the target, is not a command acknowledgement.
- Expose the current command ID, requested power, write stage, service outcome,
  readback value and timestamp, and exact error in the action sensor.
- Return to Auto after an unconfirmed power write so an inherited rate is not
  left running. Do not write power if control is disabled during mode settling.
- Add regressions for a retained 3263 W rate after a 10000 W request, failed
  number writes, delayed readback, and stale matching values.

## 0.12.3 - 2026-11-07

- Wait for Fronius mode-specific charge and discharge controls to become
  available instead of failing on their transient state immediately after a
  storage-mode transition.
- Expose the exact actuator exception on the action sensor so the GridShift UI
  can distinguish a failed write from a delayed Home Assistant state update.
- Keep physical mode and last confirmed power available for post-write
  reconciliation in the hosted dashboard.
- Persist manual quarter-hour graph overrides in Home Assistant so a selected
  charging run continues across quarter boundaries even when the browser is
  closed.

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



