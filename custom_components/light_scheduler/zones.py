"""Ownership rules between zones.

A run owns the lights it turned on: while it is active the zone ignores state
changes on them, because those are its own actuation coming back. That model
only holds while a light belongs to one zone. With a light in two zones, the
first run to finish sends turn_off, the second zone never notices -- its
listener returns early while active -- and the light goes dark before the second
schedule's own off time, with nothing reported anywhere.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def foreign_entities(
    zones: Iterable[tuple[str, Iterable[str]]],
    targets: Iterable[str],
    skip_entry_id: str | None = None,
) -> list[str]:
    """Return the targets some other zone already controls, in order.

    ``zones`` is an iterable of ``(entry_id, target_entity_ids)``.
    """
    taken: set[str] = set()
    for entry_id, entities in zones:
        if entry_id == skip_entry_id:
            continue
        taken.update(entities)
    return [entity_id for entity_id in targets if entity_id in taken]


def newly_shared_entities(
    zones: Iterable[tuple[str, Iterable[str]]],
    current_targets: Iterable[str],
    new_targets: Iterable[str],
    skip_entry_id: str | None = None,
) -> list[str]:
    """Return the overlaps this edit would introduce, ignoring existing ones.

    Rejecting every overlap outright would lock an install that already has one
    out of its own settings dialog. Only what the edit adds is refused, so a
    zone that is already sharing a light can still be edited -- and fixed.
    """
    zones = list(zones)
    already = set(foreign_entities(zones, current_targets, skip_entry_id))
    return [
        entity_id
        for entity_id in foreign_entities(zones, new_targets, skip_entry_id)
        if entity_id not in already
    ]


def zone_targets(entries: Iterable[Any]) -> list[tuple[str, list[str]]]:
    """Adapt Home Assistant config entries to what the helpers above take."""
    return [
        (entry.entry_id, list(entry.options.get("target_entity_ids", [])))
        for entry in entries
    ]
