"""Schedule normalization helpers."""
from __future__ import annotations

from datetime import time
from typing import Any
from uuid import uuid4

from .const import CONF_ENABLED, CONF_SCHEDULE_DAYS, CONF_SCHEDULE_DURATION, CONF_SCHEDULE_ID, CONF_SCHEDULE_TIME


def serialize_schedule(schedule: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-serializable schedule."""
    result = dict(schedule)
    value = result.get(CONF_SCHEDULE_TIME)
    if isinstance(value, time):
        result[CONF_SCHEDULE_TIME] = value.isoformat()
    result[CONF_SCHEDULE_DAYS] = [int(day) for day in result[CONF_SCHEDULE_DAYS]]
    result[CONF_SCHEDULE_DURATION] = int(result[CONF_SCHEDULE_DURATION])
    result[CONF_ENABLED] = bool(result.get(CONF_ENABLED, True))
    return result


def new_schedule(schedule: dict[str, Any]) -> dict[str, Any]:
    """Create a schedule with a stable generated id."""
    result = serialize_schedule(schedule)
    result[CONF_SCHEDULE_ID] = result.get(CONF_SCHEDULE_ID) or uuid4().hex
    return result
