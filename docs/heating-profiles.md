# Heating performance profiles

GridShift uses technology profiles as conservative starting points, not as a
claim that every heat pump with the same technology performs identically.

## Included profiles

| Profile | Typical behavior |
| --- | --- |
| Air-to-air heat pump | COP responds strongly to outdoor temperature. |
| Air-to-water heat pump | COP responds strongly to outdoor temperature and also depends on supply-water temperature. |
| Ground-source, borehole | COP is comparatively stable because the heat source is below ground. |
| Ground-source, horizontal loop | Moderate seasonal variation as shallow ground temperature changes. |
| Resistive or floor heating | COP fixed at 1.0. |

The selected profile defines the shape and safe bounds of the estimated COP
curve. The user-adjustable SCOP scales that curve. SCOP is therefore a calibration
input, not the COP used for every interval.

## Future equipment database

A later equipment catalog can add manufacturer and model-specific data without
changing the planner contract. Each model record should include:

- technology and rated heating capacity;
- certified SCOP and climate zone;
- COP points by outdoor and supply temperature;
- minimum modulation and auxiliary-heater thresholds;
- defrost behavior;
- source and revision date.

User measurements should be able to override catalog values. A named model must
fall back to its generic technology profile when its data is missing or stale.
