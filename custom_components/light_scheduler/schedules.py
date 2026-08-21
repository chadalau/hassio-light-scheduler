"""Schedule normalization helpers."""
from __future__ import annotations

from datetime import time
from typing import Any
from uuid import uuid4

from .const import (
    CONF_ENABLED,
    CONF_SCHEDULE_DAYS,
    CONF_SCHEDULE_DURATION,
    CONF_SCHEDULE_ID,
    CONF_SCHEDULE_INTERVAL,
    CONF_SCHEDULE_TIME,
    CONF_SCHEDULE_WARNING,
    CONF_TARGET_ENTITY_IDS,
    WARNING_TARGETS_REMOVED,
)


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
    result[CONF_SCHEDULE_WARNING] = str(result.get(CONF_SCHEDULE_WARNING) or "")
    return result


def new_schedule(schedule: dict[str, Any]) -> dict[str, Any]:
    """Create a schedule with a stable generated id."""
    result = serialize_schedule(schedule)
    result[CONF_SCHEDULE_ID] = result.get(CONF_SCHEDULE_ID) or uuid4().hex
    return result


def prune_schedule_targets(
    schedules: list[dict[str, Any]], target_entity_ids: list[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Drop lights a schedule selected but the zone no longer controls.

    A stale selection is invisible and fatal: ``_selected_targets`` narrows the
    zone by that list, so a schedule pointing only at removed lights resolves to
    nothing and never runs again, while the card keeps showing it as scheduled.

    A selection that empties out is NOT collapsed into "the whole zone" -- that
    would silently widen a schedule the user deliberately narrowed. It is
    disabled and flagged instead, so the row can explain itself.

    Returns the new list and the ids that had to be disabled.
    """
    allowed = set(target_entity_ids)
    result: list[dict[str, Any]] = []
    disabled: list[str] = []
    for schedule in schedules:
        item = dict(schedule)
        selection = list(item.get(CONF_TARGET_ENTITY_IDS) or [])
        if not selection:
            result.append(item)
            continue
        kept = [entity_id for entity_id in selection if entity_id in allowed]
        if kept == selection:
            result.append(item)
            continue
        item[CONF_TARGET_ENTITY_IDS] = kept
        if not kept:
            item[CONF_ENABLED] = False
            item[CONF_SCHEDULE_WARNING] = WARNING_TARGETS_REMOVED
            if item.get(CONF_SCHEDULE_ID):
                disabled.append(str(item[CONF_SCHEDULE_ID]))
        result.append(item)
    return result, disabled
