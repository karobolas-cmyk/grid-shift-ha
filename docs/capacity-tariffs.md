# Capacity-tariff model

Grid companies calculate demand charges in different ways. GridShift therefore
treats capacity tariffs as an explicit opt-in feature with configurable inputs,
not as a universal assumption.

## Inputs

- **Enabled:** off by default.
- **Peak model:** highest peak, top-three average, or top-five average.
- **Demand rate:** price per kW for the billing period.
- **Reference peak:** the currently recorded peak or average for the billing
  period.
- **Active hours:** the daily window in which peaks are billed. Overnight windows
  are supported.

## Planning cost

For a candidate charge interval, GridShift predicts grid power with the proposed
battery command:

$$
P_{projected}=P_{forecast\ net\ load}+P_{battery}
$$

Only power above the configured reference contributes an incremental peak:

$$
P_{increment}=\max(0, P_{projected}-P_{reference})
$$

The estimated billing impact is weighted by the selected model. A single maximum
uses 100%, a top-three average uses 1/3, and a top-five average uses 1/5. The
estimated fee is divided by the energy charged in that interval and added to the
spot price as an effective per-kWh planning cost.

This is deliberately a marginal estimate. It can move charging away from a new
peak without pretending to reproduce every grid company's final invoice.

## Operational guidance

- Copy the rate and active hours from the current grid-company tariff.
- Update the reference peak during the billing period, ideally from a Home
  Assistant helper or future grid-company integration.
- Keep phase fuse protection enabled; the tariff is an economic signal, not a
  safety limit.
- Revisit the model when the grid company changes its peak count, time window,
  seasonal rules, or averaging method.
