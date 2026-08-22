"""Status, next run and live power data for the Light Scheduler card."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_DEFAULT_DURATION,
    CONF_ENTITY_MAPPINGS,
    CONF_POWER_THRESHOLD,
    CONF_SCHEDULES,
    SIGNAL_UPDATE,
)
from .power import is_power_sensor, read_power_watts

# The card reads these from the live state; keeping them out of the recorder
# stops every power reading from writing a new row carrying the whole schedule
# list and every light's telemetry.
UNRECORDED = frozenset(
    {
        CONF_SCHEDULES,
        CONF_ENTITY_MAPPINGS,
        "lights",
        "target_entity_ids",
        "power_entity_ids",
        "schedule_warnings",
    }
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: Callable
) -> None:
    """Set up the status sensor for a light zone."""
    async_add_entities([LightScheduleStatus(entry.runtime_data, entry)])


class LightScheduleStatus(SensorEntity):
    """Expose all presentation data consumed by the custom card."""

    _attr_has_entity_name = True
    _attr_translation_key = "next_run"
    _attr_should_poll = False
    _unrecorded_attributes = UNRECORDED

    def __init__(self, scheduler: Any, entry: ConfigEntry) -> None:
        self.scheduler = scheduler
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_next_run"
        self._attr_device_info = {
            "identifiers": {(entry.domain, entry.entry_id)},
            "name": entry.title,
        }
        self._watched_entities: tuple[str, ...] = ()
        self._unsub_state_listener: Callable[[], None] | None = None
        self._cached_power_mapping: dict[str, str] | None = None
        self._cached_signature: tuple | None = None

    @property
    def native_value(self) -> str | None:
        """Return the next scheduled start as an ISO timestamp."""
        next_run = self.scheduler.next_run
        return next_run.isoformat() if next_run else None

    def _mapping_signature(self) -> tuple:
        """Return everything the discovery result depends on, cheaply."""
        return (
            tuple(self.scheduler.target_entity_ids),
            tuple(
                (item.get("target_entity_id"), item.get("power_entity_id"))
                for item in self.scheduler.entity_mappings
            ),
        )

    def _power_mapping(self) -> dict[str, str]:
        """Map each target to a power sensor, preferring the same device.

        Explicitly selected sensors are matched by Home Assistant device first.
        Positional matching remains as a backwards-compatible fallback. When no
        sensor was selected, a sensor with device_class=power on the same device
        is discovered automatically.

        Discovery walks the whole entity registry, so the result is cached and
        rebuilt only when the zone's own selection changes or the registry does
        -- not on every zone signal, which fires on each light change, each save
        and each re-arm of the timetable.
        """
        signature = self._mapping_signature()
        if self._cached_power_mapping is not None and signature == self._cached_signature:
            return dict(self._cached_power_mapping)

        registry = er.async_get(self.hass)
        targets = self.scheduler.target_entity_ids
        explicit = self.scheduler.power_entity_ids
        configured = {
            item.get("target_entity_id"): item.get("power_entity_id")
            for item in self.scheduler.entity_mappings
            if item.get("target_entity_id") and item.get("power_entity_id")
        }
        has_structured_mappings = bool(
            self.scheduler.options.get(CONF_ENTITY_MAPPINGS)
        )
        explicit_set = set(explicit)
        used: set[str] = set()
        mapping: dict[str, str] = {}

        device_power: dict[str, list[str]] = {}
        for entity in registry.entities.values():
            if entity.domain != "sensor" or not entity.device_id:
                continue
            state = self.hass.states.get(entity.entity_id)
            if is_power_sensor(state):
                device_power.setdefault(entity.device_id, []).append(entity.entity_id)

        for index, target_id in enumerate(targets):
            target_entry = registry.async_get(target_id)
            candidates = (
                device_power.get(target_entry.device_id, [])
                if target_entry and target_entry.device_id
                else []
            )
            selected = configured.get(target_id)
            selected_state = self.hass.states.get(selected) if selected else None
            if selected in used or not is_power_sensor(selected_state):
                selected = None
            if selected is None:
                selected = next(
                    (
                        entity_id
                        for entity_id in candidates
                        if entity_id in explicit_set and entity_id not in used
                    ),
                    None,
                )
            if (
                selected is None
                and not has_structured_mappings
                and index < len(explicit)
            ):
                fallback = explicit[index]
                if fallback not in used:
                    selected = fallback
            if selected is None:
                selected = next(
                    (entity_id for entity_id in candidates if entity_id not in used),
                    None,
                )
            if selected:
                mapping[target_id] = selected
                used.add(selected)
        self._cached_power_mapping = mapping
        self._cached_signature = signature
        return dict(mapping)

    def _power_watts(self, entity_id: str | None) -> float | None:
        """Return a power sensor value normalized to watts."""
        return read_power_watts(self.hass, entity_id) if entity_id else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return compact live data for the Lovelace card.

        `entity_mappings` carries what the user actually configured, never what
        was resolved for them: publishing the resolved name and the discovered
        sensor made the settings dialog show them as if they had been chosen, so
        opening the dialog and pressing save froze an automatic result into a
        manual one. The resolved values ride along under their own keys, for
        display only.
        """
        targets = self.scheduler.target_entity_ids
        power_mapping = self._power_mapping()
        last_finished_at = self.scheduler.last_finished_at
        idle_started_at = self.scheduler.idle_started_at
        configured = {
            item.get("target_entity_id"): item
            for item in self.scheduler.entity_mappings
            if item.get("target_entity_id")
        }
        lights: list[dict[str, Any]] = []
        total_power = 0.0

        for entity_id in targets:
            target = self.hass.states.get(entity_id)
            power_entity_id = power_mapping.get(entity_id)
            watts = self._power_watts(power_entity_id)
            custom_name = str(configured.get(entity_id, {}).get("name") or "").strip()
            if watts is not None:
                total_power += watts
            lights.append(
                {
                    "entity_id": entity_id,
                    "name": custom_name or (target.name if target else entity_id),
                    "state": target.state if target else STATE_UNAVAILABLE,
                    "available": bool(
                        target
                        and target.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
                    ),
                    "power_entity_id": power_entity_id,
                    "power_w": watts,
                }
            )

        return {
            "entry_id": self.entry.entry_id,
            "zone_name": self.entry.title,
            CONF_SCHEDULES: self.scheduler.options.get(CONF_SCHEDULES, []),
            "schedule_warnings": self.scheduler.schedule_warnings,
            "target_entity_ids": targets,
            "power_entity_ids": list(power_mapping.values()),
            CONF_ENTITY_MAPPINGS: [
                {
                    "name": str(configured.get(light["entity_id"], {}).get("name") or ""),
                    "target_entity_id": light["entity_id"],
                    "power_entity_id": str(
                        configured.get(light["entity_id"], {}).get("power_entity_id")
                        or ""
                    ),
                    CONF_POWER_THRESHOLD: configured.get(
                        light["entity_id"], {}
                    ).get(CONF_POWER_THRESHOLD),
                    "display_name": light["name"],
                    "resolved_power_entity_id": light["power_entity_id"] or "",
                }
                for light in lights
            ],
            CONF_DEFAULT_DURATION: self.scheduler.options.get(CONF_DEFAULT_DURATION),
            "lights": lights,
            "lights_on": sum(1 for light in lights if light["state"] == STATE_ON),
            "lights_available": sum(1 for light in lights if light["available"]),
            "total_power_w": round(total_power, 2),
            "active": self.scheduler.active,
            "stopping": self.scheduler.stopping,
            "started_at": (
                self.scheduler.started_at.isoformat()
                if self.scheduler.started_at
                else None
            ),
            "finishes_at": (
                self.scheduler.finishes_at.isoformat()
                if self.scheduler.finishes_at
                else None
            ),
            "last_finished_at": (
                last_finished_at.isoformat() if last_finished_at else None
            ),
            "idle_started_at": (
                idle_started_at.isoformat() if idle_started_at else None
            ),
            "source": self.scheduler.source,
            "enabled": self.scheduler.enabled,
        }

    @callback
    def _handle_update(self) -> None:
        self._refresh_state_listener()
        self.async_write_ha_state()

    @callback
    def _handle_registry_update(self, _: Event) -> None:
        """Rebuild discovery when entities appear, vanish or change device."""
        self._cached_power_mapping = None
        self._refresh_state_listener()
        self.async_write_ha_state()

    @callback
    def _refresh_state_listener(self) -> None:
        power_ids = tuple(self._power_mapping().values())
        watched = tuple(dict.fromkeys((*self.scheduler.target_entity_ids, *power_ids)))
        if watched == self._watched_entities:
            return
        if self._unsub_state_listener:
            self._unsub_state_listener()
        self._watched_entities = watched
        self._unsub_state_listener = (
            async_track_state_change_event(
                self.hass, list(watched), self._handle_watched_state
            )
            if watched
            else None
        )

    @callback
    def _handle_watched_state(self, event: Any) -> None:
        """Refresh mapping if a configured sensor disappears."""
        if event.data.get("new_state") is None:
            self._cached_power_mapping = None
            self._refresh_state_listener()
        self.async_write_ha_state()

    @callback
    def _remove_state_listener(self) -> None:
        if self._unsub_state_listener:
            self._unsub_state_listener()
            self._unsub_state_listener = None

    async def async_added_to_hass(self) -> None:
        """Subscribe to scheduler, target, power and registry changes."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_UPDATE.format(entry_id=self.entry.entry_id),
                self._handle_update,
            )
        )
        self.async_on_remove(
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, self._handle_registry_update
            )
        )
        self.async_on_remove(self._remove_state_listener)
        self._refresh_state_listener()
