"""Shared power-sensor helpers used by validation, display and confirmation."""
from __future__ import annotations

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State

POWER_UNITS = {"W": 1.0, "kW": 1000.0}


def is_power_sensor(state: State | None) -> bool:
    """Return whether a sensor state looks like a power measurement."""
    return bool(state) and (
        state.attributes.get("device_class") == "power"
        or state.attributes.get("unit_of_measurement") in POWER_UNITS
    )


def read_power_watts(hass: HomeAssistant, entity_id: str) -> float | None:
    """Return a power sensor's value normalized to watts, or None if unusable.

    Entities without power metadata are rejected rather than read as watts:
    a temperature paired by mistake would otherwise read as a permanent load
    and make every turn_off fail to confirm.
    """
    state = hass.states.get(entity_id)
    if (
        state is None
        or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE)
        or not is_power_sensor(state)
    ):
        return None
    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return None
    multiplier = POWER_UNITS.get(state.attributes.get("unit_of_measurement"), 1.0)
    return round(value * multiplier, 2)
