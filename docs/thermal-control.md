# Native thermostat control

Thermostat control is an independent opt-in. Configure zones from GridShift, save
the configuration to Home Assistant and enable **Automatic climate control**.
It runs locally in HA after the browser closes. No thermostat is armed by a
battery-control switch or by installing/updating the integration.

Each selected zone has an explicit baseline, maximum deviation, price response
(1–10), continuous/peak-only strategy and HVAC direction. Configure its rated
electrical power and supply phase. An unknown phase reserves the entire load on
every phase conservatively. The first release requires HA temperature units °C.

The native controller ranks published electricity prices against approximate
temperature-dependent COP. Hourly HA weather forecasts are used where available;
fresh outdoor measurements are a fallback. Generic heat-pump COP estimates are
not manufacturer performance curves. The dashboard's whole-house thermal-mass
illustration is an estimate; native per-zone targets and readback are shown
separately and do not claim measured energy savings.

Only selected `climate.*` entities can be written. Heating lowers targets at
expensive prices; cooling raises them. Targets remain inside baseline ± deviation
and the device's minimum/maximum. `heat_cool` uses a neutral band. HVAC modes are
never changed and `off`, `fan_only`, unknown and incompatible modes are preserved.
Hydronic cooling does not pre-cool without dew-point protection; it can only use
a price setback. Auxiliary heat, defrost and compressor protection remain with
the device's own controller.

There is at least five minutes between target writes across the entire home.
Matching targets are not rewritten. Increasing thermal demand requires fresh
phase data and room for the zone's full configured load. A temporary 15-minute
phase reservation is also subtracted from the battery's live charge budget.
This is conservative and can defer otherwise feasible heating or battery charge.

Service success is followed by target readback. An unmatched target is shown as
awaiting readback, then unconfirmed; the controller does not claim it was applied
or repeatedly overwrite it. A subsequent manual/other-automation change after a
confirmed command relinquishes ownership and pauses that zone for two hours.

Missing prices or weather return an owned target to its baseline. Stale indoor
or inactive HVAC data prevents a new write. On disable, reconfiguration or unload,
the previous target is restored only if it still matches GridShift's last command;
manual changes are preserved. Failed/unconfirmed restoration remains visible.

Future work: scheduled baselines for away/night, manufacturer COP maps, humidity
and dew-point control, and explicitly measured thermal energy savings.
