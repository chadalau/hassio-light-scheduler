"""Event driven scheduling of groups of Home Assistant lights."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CoreState, Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_point_in_time, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (CONF_DEFAULT_DURATION, CONF_ENABLED, CONF_ENTITY_MAPPINGS, CONF_MAX_DURATION, CONF_POWER_ENTITY_IDS,
                    CONF_SCHEDULE_DAYS, CONF_SCHEDULE_DURATION, CONF_SCHEDULE_INTERVAL, CONF_SCHEDULE_TIME, CONF_SCHEDULES,
                    CONF_TARGET_ENTITY_IDS, HISTORY_MAX_ENTRIES, HISTORY_RETENTION_DAYS, SIGNAL_UPDATE,
                    MAX_SCHEDULE_INTERVAL, SOURCE_EXTERNAL, SOURCE_MANUAL, SOURCE_SCHEDULE)
from .store import RuntimeStore
from .next_run import find_next_run

_LOGGER = logging.getLogger(__name__)


class LightScheduler:
    """Manage schedules, execution and telemetry for one light group."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, store: RuntimeStore) -> None:
        self.hass, self.entry, self.store = hass, entry, store
        self._active = False
        self._stopping = False
        self._run_interval = 0
        self._run_targets: list[str] = []
        self._source: str | None = None
        self._started_at: datetime | None = None
        self._finishes_at: datetime | None = None
        self._history: list[dict[str, Any]] = []
        self._unsub_next = None
        self._unsub_finish = None
        self._unsub_states = None
        self._unsub_started = None
        self._next_run: datetime | None = None
        self._next_schedule: dict[str, Any] | None = None
        self._schedule_generation = 0
        self._background_tasks: set[asyncio.Task] = set()
        self._ramp_task: asyncio.Task | None = None
        self._unloading = False

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
    def stopping(self) -> bool:
        return self._stopping

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
        """Restore runtime data and subscribe to scheduler and light activity."""
        stored = await self.store.async_get(self.entry.entry_id)
        self._history = list(stored.get("history", []))[-HISTORY_MAX_ENTRIES:]
        self._restore_active_run(stored.get("active_run"))
        self._unsub_states = async_track_state_change_event(self.hass, self.target_entity_ids, self._on_light_changed)
        if self.hass.state is CoreState.running:
            await self._async_reconcile_after_start(None)
        else:
            self._unsub_started = self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, self._async_reconcile_after_start
            )

    async def async_unload(self) -> None:
        """Cancel all work and leave no active light run behind."""
        self._unloading = True
        if self._active:
            await self.async_stop(schedule_next=False)
        for unsub in (
            self._unsub_next,
            self._unsub_finish,
            self._unsub_states,
            self._unsub_started,
        ):
            if unsub:
                unsub()
        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)

    async def _async_reconcile_after_start(self, _: Event | None) -> None:
        """Resume a persisted run or safely finish an expired one."""
        self._unsub_started = None
        if self._active and self._finishes_at:
            if self._finishes_at <= dt_util.utcnow():
                await self.async_stop(schedule_next=False)
            else:
                self._arm_finish_timer()
        self._schedule_next()

    def _restore_active_run(self, value: Any) -> None:
        """Restore a persisted active run without touching devices yet."""
        if not isinstance(value, dict):
            return
        started = dt_util.parse_datetime(str(value.get("started_at") or ""))
        finishes = dt_util.parse_datetime(str(value.get("finishes_at") or ""))
        if not started or not finishes:
            return
        self._active = True
        self._started_at = dt_util.as_utc(started)
        self._finishes_at = dt_util.as_utc(finishes)
        self._source = str(value.get("source") or SOURCE_SCHEDULE)
        self._run_interval = max(
            0, min(int(value.get("interval") or 0), MAX_SCHEDULE_INTERVAL)
        )
        stored_targets = value.get("targets")
        self._run_targets = (
            [str(entity_id) for entity_id in stored_targets]
            if isinstance(stored_targets, list)
            else list(self.target_entity_ids)
        )

    def _arm_finish_timer(self) -> None:
        if self._unsub_finish:
            self._unsub_finish()
        self._unsub_finish = async_track_point_in_time(
            self.hass, self._async_finish, self._finishes_at
        )

    def _create_background_task(self, coroutine: Any) -> None:
        task = self.hass.async_create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    @callback
    def _on_light_changed(self, event: Event) -> None:
        if self._active:
            return
        new_state = event.data.get("new_state")
        if new_state and new_state.state == STATE_ON:
            self._create_background_task(
                self._record_external(event.data["entity_id"], True)
            )
        elif new_state:
            self._create_background_task(
                self._record_external(event.data["entity_id"], False)
            )
        self._notify()

    async def _record_external(self, entity_id: str, is_on: bool) -> None:
        now = dt_util.utcnow()
        if is_on:
            if any(
                item.get("source") == SOURCE_EXTERNAL
                and item.get("entity_id") == entity_id
                and not item.get("finished_at")
                for item in self._history
            ):
                return
            self._history.append({"started_at": now.isoformat(), "finished_at": None,
                                  "duration": None, "source": SOURCE_EXTERNAL, "entity_id": entity_id})
        else:
            for item in reversed(self._history):
                if (
                    item.get("source") == SOURCE_EXTERNAL
                    and item.get("entity_id") == entity_id
                    and not item.get("finished_at")
                ):
                    raw_started = item.get("started_at")
                    started = (
                        dt_util.parse_datetime(raw_started)
                        if isinstance(raw_started, str)
                        else None
                    )
                    item["finished_at"] = now.isoformat()
                    item["duration"] = (
                        max(0, int((now - dt_util.as_utc(started)).total_seconds()))
                        if started
                        else None
                    )
                    break
            else:
                return
        await self._save_history()

    def _schedule_next(self) -> None:
        if self._unsub_next:
            self._unsub_next(); self._unsub_next = None
        self._schedule_generation += 1
        if not self.enabled or self._stopping or self._unloading:
            self._next_run = None
            self._next_schedule = None
            self._notify(); return
        upcoming, schedule = find_next_run(
            self.options.get(CONF_SCHEDULES, []), dt_util.now(), self.enabled
        )
        self._next_run = upcoming
        self._next_schedule = dict(schedule) if schedule else None
        if upcoming is not None:
            generation = self._schedule_generation

            async def scheduled_start(_: datetime) -> None:
                if generation != self._schedule_generation:
                    return
                await self._async_scheduled_start(upcoming, schedule)

            self._unsub_next = async_track_point_in_time(
                self.hass, scheduled_start, upcoming
            )
        self._notify()

    @property
    def next_run(self) -> datetime | None:
        return self._next_run

    async def _async_scheduled_start(
        self, scheduled_at: datetime, schedule: dict[str, Any] | None
    ) -> None:
        self._next_run = None
        self._next_schedule = None
        if schedule is not None:
            if self._active:
                candidate_finish = dt_util.as_utc(scheduled_at) + timedelta(
                    seconds=int(schedule[CONF_SCHEDULE_DURATION])
                )
                if not self._stopping and (
                    self._finishes_at is None or candidate_finish > self._finishes_at
                ):
                    self._finishes_at = candidate_finish
                    self._arm_finish_timer()
                    await self._save_runtime(immediate=True)
                self._schedule_next()
                return
            await self.async_turn_on(
                duration=int(schedule[CONF_SCHEDULE_DURATION]),
                source=SOURCE_SCHEDULE,
                interval=int(schedule.get(CONF_SCHEDULE_INTERVAL, 0)),
            )
        else:
            self._schedule_next()

    async def async_turn_on(
        self,
        duration: int | None = None,
        source: str = SOURCE_MANUAL,
        interval: int = 0,
    ) -> None:
        """Turn configured lights on sequentially for a bounded duration."""
        if self._active:
            # Repeated clicks and overlapping schedules must never invert the
            # current run. The dedicated stop action is the only way to end it.
            return
        max_duration = int(self.options.get(CONF_MAX_DURATION, 86400))
        seconds = duration if duration is not None else int(self.options.get(CONF_DEFAULT_DURATION, 14400))
        seconds = max(1, min(int(seconds), max_duration))
        interval = max(0, min(int(interval), MAX_SCHEDULE_INTERVAL))
        targets: list[str] = []
        for entity_id in self.target_entity_ids:
            state = self.hass.states.get(entity_id)
            if state and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                targets.append(entity_id)
        if not targets:
            _LOGGER.warning("No available lights in %s", self.entry.title)
            self._schedule_next(); return
        self._active, self._source = True, source
        self._stopping = False
        self._run_interval = interval
        self._run_targets = list(targets)
        self._started_at = dt_util.utcnow()
        self._finishes_at = self._started_at + timedelta(seconds=seconds)
        self._arm_finish_timer()
        await self._save_runtime(immediate=True)
        self._schedule_next()

        # Arm the requested start of the shutdown sequence before ramping up.
        # The same interval is then used to turn the lights off in order.
        current_task = asyncio.current_task()
        self._ramp_task = current_task
        try:
            for index, entity_id in enumerate(targets):
                if not self._active or self._stopping or self._unloading:
                    break
                try:
                    await self.hass.services.async_call(
                        "homeassistant",
                        "turn_on",
                        {"entity_id": entity_id},
                        blocking=True,
                    )
                except Exception:  # Home Assistant reports the failed entity.
                    _LOGGER.exception(
                        "Unable to turn on %s in %s", entity_id, self.entry.title
                    )
                if interval and index < len(targets) - 1 and self._active and not self._stopping:
                    remaining = (self._finishes_at - dt_util.utcnow()).total_seconds()
                    if remaining <= 0:
                        break
                    await asyncio.sleep(min(interval, remaining))
        finally:
            if self._ramp_task is current_task:
                self._ramp_task = None

    async def _async_finish(self, _: datetime) -> None:
        await self.async_stop()

    async def async_stop(
        self, interval: int | None = None, *, schedule_next: bool = True
    ) -> None:
        """Stop an execution using the same ordered interval as startup."""
        if not self._active or self._stopping:
            return
        self._stopping = True
        ramp_task = self._ramp_task
        current_task = asyncio.current_task()
        if ramp_task and ramp_task is not current_task and not ramp_task.done():
            ramp_task.cancel()
            await asyncio.gather(ramp_task, return_exceptions=True)
        interval = self._run_interval if interval is None else interval
        interval = max(0, min(int(interval), MAX_SCHEDULE_INTERVAL))
        if self._unsub_finish:
            self._unsub_finish(); self._unsub_finish = None
        self._notify()
        targets = list(self._run_targets or self.target_entity_ids)
        for index, entity_id in enumerate(targets):
            try:
                await self.hass.services.async_call(
                    "homeassistant",
                    "turn_off",
                    {"entity_id": entity_id},
                    blocking=True,
                )
            except Exception:
                _LOGGER.exception(
                    "Unable to turn off %s in %s", entity_id, self.entry.title
                )
            if interval and index < len(targets) - 1:
                await asyncio.sleep(interval)
        started, source = self._started_at, self._source
        finished = dt_util.utcnow()
        self._history.append({"started_at": started.isoformat() if started else None, "finished_at": finished.isoformat(),
                              "duration": int((finished - started).total_seconds()) if started else None, "source": source})
        self._active = False; self._stopping = False; self._run_interval = 0
        self._run_targets = []
        self._source = None; self._started_at = None; self._finishes_at = None
        self._prune_history()
        await self._save_runtime(immediate=True)
        if schedule_next:
            self._schedule_next()

    async def _save_history(self) -> None:
        self._prune_history()
        await self._save_runtime()

    def _prune_history(self) -> None:
        """Remove expired history records with a single timestamp parse."""
        cutoff = dt_util.utcnow() - timedelta(days=HISTORY_RETENTION_DAYS)
        retained: list[dict[str, Any]] = []
        for item in self._history:
            raw_started = item.get("started_at")
            started = (
                dt_util.parse_datetime(raw_started)
                if isinstance(raw_started, str)
                else None
            )
            if not started or dt_util.as_utc(started) >= cutoff:
                retained.append(item)
        self._history = retained[-HISTORY_MAX_ENTRIES:]

    async def _save_runtime(self, *, immediate: bool = False) -> None:
        active_run = None
        if self._active and self._started_at and self._finishes_at:
            active_run = {
                "started_at": self._started_at.isoformat(),
                "finishes_at": self._finishes_at.isoformat(),
                "source": self._source,
                "interval": self._run_interval,
                "targets": self._run_targets,
            }
        await self.store.async_set(
            self.entry.entry_id,
            {"history": self._history, "active_run": active_run},
            immediate=immediate,
        )
        self._notify()

    async def async_set_enabled(self, enabled: bool) -> None:
        options = {**self.options, CONF_ENABLED: enabled}
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        if not enabled and self._active:
            await self.async_stop()

    async def async_options_updated(self) -> None:
        if self._unsub_states:
            self._unsub_states()
        self._unsub_states = async_track_state_change_event(self.hass, self.target_entity_ids, self._on_light_changed)
        self._schedule_next()

    def _notify(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(entry_id=self.entry.entry_id))
