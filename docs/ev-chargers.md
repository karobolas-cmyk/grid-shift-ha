# EV charger integrations

GridShift integrates with chargers through Home Assistant entities rather than
calling vendor clouds directly. This keeps credentials and device communication
inside Home Assistant and makes local integrations usable where available.

## Current scope

GridShift can discover, select, and sum live power sensors from:

- Charge Amps;
- Easee;
- Zaptec;
- Sigenergy EVAC and EVDC;
- Wallbox;
- Tesla Wall Connector;
- go-e;
- myenergi Zappi;
- OCPP charge points;
- Fronius Wattpilot;
- KEBA KeContact;
- OpenEVSE;
- Ohme;
- Peblar;
- Amina;
- any manually selected Home Assistant power sensor in W, kW, or MW.

Multiple chargers are summed before the load forecast is learned. Keep the
`house_load_includes_ev` option enabled when the whole-home meter already includes
charging, which is the normal case. Disable it only when the selected house-load
entity explicitly excludes EV charging.

## Safety boundary

Version 0.4 monitors charger power but does not write charger commands. Charger
integrations expose different control contracts: switches, buttons, number
entities, select entities, or custom services. A later control layer should map
these to a common start/stop and current-limit interface, remain disabled by
default, and validate phase headroom before every command.

Sigenergy is also available as a solar, battery, load, and grid source. Its local
Modbus integration is preferred when available; cloud entities can still be
selected manually.
