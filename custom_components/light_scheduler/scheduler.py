"""Event driven scheduling of groups of Home Assistant lights."""
from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_point_in_time, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (CONF_DEFAULT_DURATION, CONF_ENABLED, CONF_ENTITY_MAPPINGS, CONF_MAX_DURATION, CONF_POWER_ENTITY_IDS,
                    CONF_SCHEDULE_DAYS, CONF_SCHEDULE_DURATION, CONF_SCHEDULE_TIME, CONF_SCHEDULES,
                    CONF_TARGET_ENTITY_IDS, HISTORY_MAX_ENTRIES, HISTORY_RETENTION_DAYS, SIGNAL_UPDATE,
                    SOURCE_EXTERNAL, SOURCE_MANUAL, SOURCE_SCHEDULE)
from .store import RuntimeStore
from .next_run import find_next_run

_LOGGER = logging.getLogger(__name__)


class LightScheduler:
    """Manage schedules, execution and telemetry for one light group."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, store: RuntimeStore) -> None:
        self.hass, self.entry, self.store = hass, entry, store
        self._active = False
        self._source: str | None = None
        self._started_at: datetime | None = None
        self._finishes_at: datetime | None = None
        self._history: list[dict[str, Any]] = []
        self._unsub_next = None
        self._unsub_finish = None
        self._unsub_states = None
        self._next_run: datetime | None = None
        self._next_schedule: dict[str, Any] | None = None

    @property
    def options(self) -> dict[str, Any]:
        return self.entry.options

    @property
    def target_entity_ids(self) -> list[str]:
        mappings = self.options.get(CONF_ENTITY_MAPPINGS, [])
        if mappings:
            return [
                item["target_entity_id"]
                for item in mappings
                if isinstance(item, dict) and item.get("target_entity_id")
            ]
        return list(self.options.get(CONF_TARGET_ENTITY_IDS, []))

    @property
    def power_entity_ids(self) -> list[str]:
        mappings = self.options.get(CONF_ENTITY_MAPPINGS, [])
        if mappings:
            return [
                item["power_entity_id"]
                for item in mappings
                if isinstance(item, dict) and item.get("power_entity_id")
            ]
        return list(self.options.get(CONF_POWER_ENTITY_IDS, []))

    @property
    def entity_mappings(self) -> list[dict[str, Any]]:
        """Return ordered target, power and custom-name mappings."""
        mappings = self.options.get(CONF_ENTITY_MAPPINGS, [])
        if mappings:
            return [dict(item) for item in mappings if isinstance(item, dict)]
        power_ids = list(self.options.get(CONF_POWER_ENTITY_IDS, []))
        return [
            {
                "name": "",
                "target_entity_id": entity_id,
                "power_entity_id": power_ids[index] if index < len(power_ids) else "",
            }
            for index, entity_id in enumerate(
                self.options.get(CONF_TARGET_ENTITY_IDS, [])
            )
        ]

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_ENABLED, True))

    @property
    def active(self) -> bool:
        return self._active

    @property
    def source(self) -> str | None:
        return self._source

    @property
    def started_at(self) -> datetime | None:
        return self._started_at

    @property
    def finishes_at(self) -> datetime | None:
        return self._finishes_at

    @property
    def history(self) -> list[dict[str, Any]]:
        return self._history

    async def async_setup(self) -> None:
        """Restore history and subscribe to scheduler and external activity."""
        stored = await self.store.async_get(self.entry.entry_id)
        self._history = list(stored.get("history", []))[-HISTORY_MAX_ENTRIES:]
        self._unsub_states = async_track_state_change_event(self.hass, self.target_entity_ids, self._on_light_changed)
        self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, self._async_reconcile_after_start)
        self._schedule_next()
        self._notify()

    async def async_unload(self) -> None:
        for unsub in (self._unsub_next, self._unsub_finish, self._unsub_states):
            if unsub:
                unsub()

    async def _async_reconcile_after_start(self, _: Event) -> None:
        """Do not infer stale active runs; only make sure upcoming work is armed."""
        self._schedule_next()

    @callback
    def _on_light_changed(self, event: Event) -> None:
        if self._active:
            return
        new_state = event.data.get("new_state")
        if new_state and new_state.state == STATE_ON:
            self.hass.async_create_task(self._record_external(event.data["entity_id"]))
        self._notify()

    async def _record_external(self, entity_id: str) -> None:
        self._history.append({"started_at": dt_util.utcnow().isoformat(), "finished_at": None,
                              "duration": None, "source": SOURCE_EXTERNAL, "entity_id": entity_id})
        await self._save_history()

    def _schedule_next(self) -> None:
        if self._unsub_next:
            self._unsub_next(); self._unsub_next = None
        if not self.enabled or self._active:
            self._notify(); return
        upcoming = self.next_run
        if upcoming is not None:
            self._unsub_next = async_track_point_in_time(self.hass, self._async_scheduled_start, upcoming)
        self._notify()

    @property
    def next_run(self) -> datetime | None:
        self._next_run, self._next_schedule = find_next_run(
            self.options.get(CONF_SCHEDULES, []), dt_util.now(), self.enabled
        )
        return self._next_run

    async def _async_scheduled_start(self, _: datetime) -> None:
        schedule = self._next_schedule
        self._next_run = None
        self._next_schedule = None
        if schedule is not None:
            await self.async_turn_on(duration=int(schedule[CONF_SCHEDULE_DURATION]), source=SOURCE_SCHEDULE)
        else:
            self._schedule_next()

    async def async_turn_on(self, duration: int | None = None, source: str = SOURCE_MANUAL) -> None:
        """Turn every configured light on for a bounded duration."""
        if self._active:
            # Repeated clicks and overlapping schedules must never invert the
            # current run. The dedicated stop action is the only way to end it.
            return
        max_duration = int(self.options.get(CONF_MAX_DURATION, 86400))
        seconds = duration if duration is not None else int(self.options.get(CONF_DEFAULT_DURATION, 14400))
        seconds = max(1, min(int(seconds), max_duration))
        targets = [entity for entity in self.target_entity_ids if self.hass.states.get(entity) and self.hass.states.get(entity).state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)]
        if not targets:
            _LOGGER.warning("No available lights in %s", self.entry.title)
            self._schedule_next(); return
        # ``homeassistant.turn_on`` supports both native ``light`` entities
        # and the smart-plug/relay ``switch`` entities used by many lamps.
        await self.hass.services.async_call("homeassistant", "turn_on", {"entity_id": targets}, blocking=True)
        self._active, self._source = True, source
        self._started_at = dt_util.utcnow()
        self._finishes_at = self._started_at + timedelta(seconds=seconds)
        if self._unsub_finish:
            self._unsub_finish()
        self._unsub_finish = async_track_point_in_time(self.hass, self._async_finish, self._finishes_at)
        self._notify()

    async def _async_finish(self, _: datetime) -> None:
        await self.async_stop()

    async def async_stop(self) -> None:
        """Stop only executions started by this integration."""
        if not self._active:
            return
        if self._unsub_finish:
            self._unsub_finish(); self._unsub_finish = None
        await self.hass.services.async_call("homeassistant", "turn_off", {"entity_id": self.target_entity_ids}, blocking=True)
        started, source = self._started_at, self._source
        finished = dt_util.utcnow()
        self._history.append({"started_at": started.isoformat() if started else None, "finished_at": finished.isoformat(),
                              "duration": int((finished - started).total_seconds()) if started else None, "source": source})
        self._active = False; self._source = None; self._started_at = None; self._finishes_at = None
        await self._save_history()
        self._schedule_next()

    async def _save_history(self) -> None:
        cutoff = dt_util.utcnow() - timedelta(days=HISTORY_RETENTION_DAYS)
        self._history = [item for item in self._history if not item.get("started_at") or dt_util.parse_datetime(item["started_at"]) is None or dt_util.parse_datetime(item["started_at"]) >= cutoff][-HISTORY_MAX_ENTRIES:]
        await self.store.async_set(self.entry.entry_id, {"history": self._history})
        self._notify()

    async def async_set_enabled(self, enabled: bool) -> None:
        options = {**self.options, CONF_ENABLED: enabled}
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        if not enabled and self._active:
            await self.async_stop()
        self._schedule_next()

    async def async_options_updated(self) -> None:
        if self._unsub_states:
            self._unsub_states()
        self._unsub_states = async_track_state_change_event(self.hass, self.target_entity_ids, self._on_light_changed)
        self._schedule_next()

    def _notify(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(entry_id=self.entry.entry_id))
