"""Schedule normalization helpers."""
from __future__ import annotations

from datetime import time
from typing import Any
from uuid import uuid4

from .const import (CONF_ENABLED, CONF_SCHEDULE_DAYS, CONF_SCHEDULE_DURATION, CONF_SCHEDULE_ID,
                    CONF_SCHEDULE_INTERVAL, CONF_SCHEDULE_TIME, CONF_TARGET_ENTITY_IDS)


def serialize_schedule(schedule: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-serializable schedule.

    An empty target list means "every light in the zone", so a light added
    to the zone later is picked up by schedules the user never narrowed.
    """
    result = dict(schedule)
    value = result.get(CONF_SCHEDULE_TIME)
    if isinstance(value, time):
        result[CONF_SCHEDULE_TIME] = value.strftime("%H:%M")
    result[CONF_SCHEDULE_DAYS] = [int(day) for day in result[CONF_SCHEDULE_DAYS]]
    result[CONF_TARGET_ENTITY_IDS] = list(
        dict.fromkeys(
            str(entity_id)
            for entity_id in (result.get(CONF_TARGET_ENTITY_IDS) or [])
        )
    )
    result[CONF_SCHEDULE_DURATION] = int(result[CONF_SCHEDULE_DURATION])
    result[CONF_SCHEDULE_INTERVAL] = int(result.get(CONF_SCHEDULE_INTERVAL, 0))
    result[CONF_ENABLED] = bool(result.get(CONF_ENABLED, True))
    return result


def new_schedule(schedule: dict[str, Any]) -> dict[str, Any]:
    """Create a schedule with a stable generated id."""
    result = serialize_schedule(schedule)
    result[CONF_SCHEDULE_ID] = result.get(CONF_SCHEDULE_ID) or uuid4().hex
    return result
