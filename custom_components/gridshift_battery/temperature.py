"""Read each thermostat's supported temperature range."""

from math import isfinite


def temperature_limits(state, defaults=(14.0, 30.0)) -> tuple[float, float]:
    """Use reported limits; retain conservative defaults only for missing attributes."""
    attributes = state.attributes if state is not None else {}
    limits = []
    for name, fallback in zip(("min_temp", "max_temp"), defaults, strict=True):
        raw = attributes.get(name)
        if raw is None:
            raw = fallback
        try:
            value = float(raw)
        except (TypeError, ValueError, OverflowError) as err:
            raise ValueError("invalid_device_temperature_limits") from err
        if isinstance(raw, bool) or not isfinite(value):
            raise ValueError("invalid_device_temperature_limits")
        limits.append(value)
    if limits[0] > limits[1]:
        raise ValueError("invalid_device_temperature_limits")
    return tuple(limits)
