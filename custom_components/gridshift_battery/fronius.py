"""Fronius storage-control compatibility helpers."""

from __future__ import annotations

from collections.abc import Iterable


def missing_control_modes(
    available_options: Iterable[str],
    *,
    auto_mode: str,
    charge_mode: str,
    discharge_mode: str | None = None,
) -> tuple[str, ...]:
    """Return configured GridShift modes that the Fronius select does not expose."""
    available = set(available_options)
    required = [auto_mode, charge_mode]
    if discharge_mode:
        required.append(discharge_mode)
    return tuple(mode for mode in required if mode not in available)
