# Thermal control model

The thermal model treats every `climate` entity as a separate zone. The hosted
hosted preview is read-only; this document defines the safety contract required before
physical climate control is added to the Home Assistant integration.

## Zone configuration

Every selected zone has explicit settings:

- **Baseline:** normal setpoint without price pressure, for example 22.0 °C.
- **Maximum deviation:** largest allowed distance from the baseline.
- **Price response 1–10:** how much of the maximum deviation may be used.
- **Price strategy:** continuous scale or only the most expensive intervals.
- **HVAC direction:** automatic detection or explicit `heat`, `cool`, or
  `heat_cool`.

Without an active price or preheating signal, the recommendation returns exactly
to the baseline. `heat` lowers the setpoint during expensive intervals, `cool`
raises it, and `heat_cool` widens the range symmetrically around the baseline.
`off`, `fan_only`, and unknown modes are left unchanged.

## Safety requirements before physical control

1. Only explicitly selected entity IDs may be action targets.
2. Baseline and maximum deviation must be checked in both the planner and just
   before each Home Assistant action.
3. Missing or stale price, weather, indoor-temperature, or HVAC data must return
   to the baseline/fallback, never an aggressive temperature change.
4. Setpoint changes need a deadband, maximum rate of change, and minimum run/stop
   times to avoid short cycling.
5. Cooling needs humidity and dew-point limits to avoid condensation risk.
6. Auxiliary heat, defrost cycles, and unit-specific COP curves must be modeled
   separately from nominal SCOP.
7. Thermal demand must share the same phase forecast as the battery and EV before
   multiple loads are allowed to start together.

## Next model step

A single baseline is sufficient for the first preview. Later, the active baseline
should come from a schedule or a Home Assistant state such as home, night, away,
or vacation. The user's schedule remains the primary signal; price optimization
may only make a bounded deviation from that active baseline.
