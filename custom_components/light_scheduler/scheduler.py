"""Event driven scheduling of groups of Home Assistant lights."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (EVENT_HOMEASSISTANT_STARTED, STATE_OFF, STATE_ON, STATE_UNAVAILABLE,
                                 STATE_UNKNOWN)
from homeassistant.core import CoreState, Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_point_in_time, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (ACTUATION_GRACE, CONF_DEFAULT_DURATION, CONF_ENABLED, CONF_ENTITY_MAPPINGS, CONF_MAX_DURATION,
                    CONF_POWER_ENTITY_IDS, CONF_POWER_THRESHOLD, CONF_SCHEDULE_DURATION, CONF_SCHEDULE_ID,
                    CONF_SCHEDULE_INTERVAL, CONF_SCHEDULES, CONF_TARGET_ENTITY_IDS,
                    DEFAULT_POWER_THRESHOLD_W, HISTORY_MAX_ENTRIES, HISTORY_RETENTION_DAYS, MAX_SCHEDULE_INTERVAL,
                    SERVICE_CALL_TIMEOUT, SIGNAL_UPDATE, SOURCE_EXTERNAL, SOURCE_MANUAL, SOURCE_SCHEDULE,
                    WARNING_AMBIGUOUS_TIME)
from .power import read_power_watts
from .store import RuntimeStore
from .next_run import ambiguous_schedule_ids, find_next_run

_LOGGER = logging.getLogger(__name__)


class LightScheduler:
    """Manage schedules, execution and telemetry for one light group."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, store: RuntimeStore) -> None:
        self.hass, self.entry, self.store = hass, entry, store
        self._active = False
        self._stopping = False
        self._run_interval = 0
        self._run_targets: list[str] = []
        self._run_warnings: list[str] = []
        self._run_schedule_id: str | None = None
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
        self._stop_task: asyncio.Task | None = None
        self._unloading = False
        # A schedule that fired while the shutdown sequence still owned the zone.
        # It is replayed once the zone is idle instead of being dropped.
        self._pending_start: tuple[datetime, dict[str, Any]] | None = None
        self._schedule_warnings: dict[str, str] = {}
        # Every option mutation is a read-modify-write over entry.options.
        # Without this, two concurrent card actions read the same list and the
        # second write erases the first.
        self._options_lock = asyncio.Lock()

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

    @property
    def warnings(self) -> list[str]:
        """Entities that did not confirm their requested state this run."""
        return list(self._run_warnings)

    @property
    def schedule_warnings(self) -> dict[str, str]:
        """Reason codes for schedules that need the user's attention.

        Persisted reasons (a selection the zone no longer controls) live on the
        schedule itself; the ones computed here depend on the calendar and would
        go stale if stored.
        """
        return dict(self._schedule_warnings)

    @property
    def options_lock(self) -> asyncio.Lock:
        """Serialize option mutations for this entry."""
        return self._options_lock

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
        self._pending_start = None
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
        self._run_schedule_id = str(value["schedule_id"]) if value.get("schedule_id") else None
        # Lights dropped from the zone while this run was persisted are
        # discarded: keeping them would make the stop sequence burn a full
        # grace period plus a retry confirming an entity that cannot answer.
        # Falling back to the whole zone is safe because recovery only ever
        # turns targets off.
        stored_targets = value.get("targets")
        configured = self.target_entity_ids
        self._run_targets = (
            [
                str(entity_id)
                for entity_id in stored_targets
                if str(entity_id) in configured
            ]
            or list(configured)
            if isinstance(stored_targets, list)
            else list(configured)
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
        elif new_state and new_state.state == STATE_OFF:
            # Only a real off closes the record. Going unavailable/unknown is
            # a loss of contact, not a confirmed shutdown, and closing on it
            # would invent a duration and split the next run in two.
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
        schedules = self.options.get(CONF_SCHEDULES, [])
        now = dt_util.now()
        self._schedule_warnings = {
            schedule_id: WARNING_AMBIGUOUS_TIME
            for schedule_id in ambiguous_schedule_ids(schedules, now)
        }
        upcoming, schedule = find_next_run(schedules, now, self.enabled)
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
            if self._stopping and not self._unloading:
                # The shutdown sequence still owns the zone: it holds _active
                # while it staggers the lights off, which blocks turning on,
                # blocks extending the off time and blocks adding targets. With
                # a 300 s interval that window is twenty minutes wide, so the
                # start is replayed after the stop instead of being lost.
                self._pending_start = (scheduled_at, dict(schedule))
                _LOGGER.info(
                    "Schedule %s in %s fired during the shutdown sequence; "
                    "it will start as soon as the zone is idle",
                    schedule.get(CONF_SCHEDULE_ID), self.entry.title,
                )
                self._schedule_next()
                return
            if self._active:
                candidate_finish = dt_util.as_utc(scheduled_at) + timedelta(
                    seconds=int(schedule[CONF_SCHEDULE_DURATION])
                )
                if self._finishes_at is None or candidate_finish > self._finishes_at:
                    self._finishes_at = candidate_finish
                    self._arm_finish_timer()
                    await self._save_runtime(immediate=True)
                await self._async_extend_targets(schedule)
                self._schedule_next()
                return
            await self.async_turn_on(
                duration=int(schedule[CONF_SCHEDULE_DURATION]),
                source=SOURCE_SCHEDULE,
                interval=int(schedule.get(CONF_SCHEDULE_INTERVAL, 0)),
                schedule_id=schedule.get(CONF_SCHEDULE_ID),
                entity_ids=schedule.get(CONF_TARGET_ENTITY_IDS),
            )
        else:
            self._schedule_next()

    def _selected_targets(self, entity_ids: list[str] | None) -> list[str]:
        """Narrow the zone to a selection, keeping the zone's own order.

        An empty or missing selection means the whole zone, so a light added
        to the zone later joins schedules the user never narrowed.
        """
        selected = set(entity_ids or ())
        return [
            entity_id
            for entity_id in self.target_entity_ids
            if not selected or entity_id in selected
        ]

    def _available(self, entity_ids: list[str]) -> list[str]:
        """Keep only entities Home Assistant can currently act on."""
        available: list[str] = []
        for entity_id in entity_ids:
            state = self.hass.states.get(entity_id)
            if state and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                available.append(entity_id)
        return available

    async def _async_extend_targets(self, schedule: dict[str, Any]) -> None:
        """Turn on the lights a colliding schedule adds to the running one.

        Extending only the off time was enough while every schedule drove the
        whole zone. Now that a schedule carries its own selection, a wider
        schedule firing over a narrower run must also light what it adds, or
        those lights stay dark for the entire extended run and are never
        turned off at the end either.

        The additions are not staggered: the interval exists to spread inrush
        at startup, and holding this timer callback open for a full stagger
        would delay the rest of the run's bookkeeping.
        """
        if self._stopping or self._unloading or not self._active:
            return
        added = self._available(
            [
                entity_id
                for entity_id in self._selected_targets(
                    schedule.get(CONF_TARGET_ENTITY_IDS)
                )
                if entity_id not in self._run_targets
            ]
        )
        if not added:
            return
        self._run_targets.extend(added)
        await self._save_runtime(immediate=True)
        for entity_id in added:
            if self._stopping or self._unloading:
                return
            await self._dispatch(entity_id, "turn_on")
        unconfirmed = await self._confirm_group(added, "turn_on", True)
        if unconfirmed:
            self._run_warnings = list(
                dict.fromkeys([*self._run_warnings, *unconfirmed])
            )
            self._notify()

    def _power_entity_for(self, entity_id: str) -> str | None:
        """Return the power sensor explicitly paired with a target, if any."""
        return next(
            (
                item.get("power_entity_id")
                for item in self.entity_mappings
                if item.get("target_entity_id") == entity_id and item.get("power_entity_id")
            ),
            None,
        )

    def _power_threshold_for(self, entity_id: str) -> float:
        """Return the watts above which this target counts as drawing current.

        The default suits a normal bulb, but a sub-watt LED strip would never
        confirm turning on against it, paying a pointless resend plus two grace
        windows on every single run. Each entry can lower or raise it.
        """
        for item in self.entity_mappings:
            if item.get("target_entity_id") != entity_id:
                continue
            try:
                value = float(item.get(CONF_POWER_THRESHOLD))
            except (TypeError, ValueError):
                break
            if value > 0:
                return value
            break
        return DEFAULT_POWER_THRESHOLD_W

    def _is_confirmed(self, entity_id: str, expect_on: bool, power_entity_id: str | None) -> bool:
        """Check the entity's own state and, when paired, its power reading.

        A power sensor is the only way to catch a light that reports "on" in
        Home Assistant but never actually drew current (dead bulb, tripped
        breaker, lost mesh command). It is skipped whenever no sensor is
        paired, or its reading is temporarily unavailable.
        """
        state = self.hass.states.get(entity_id)
        if state is None or state.state != (STATE_ON if expect_on else STATE_OFF):
            return False
        if not power_entity_id:
            return True
        watts = read_power_watts(self.hass, power_entity_id)
        if watts is None:
            return True
        return (watts > self._power_threshold_for(entity_id)) == expect_on

    async def _dispatch(self, entity_id: str, service: str) -> None:
        """Send one turn_on/turn_off call without waiting for confirmation.

        The call is bounded: a device integration that never returns would
        otherwise stall the whole shutdown sequence, the finish timer and
        unload behind a single unresponsive entity. A timeout is reported and
        the sequence moves on; confirmation still decides whether the entity
        actually obeyed.
        """
        try:
            async with asyncio.timeout(SERVICE_CALL_TIMEOUT):
                await self.hass.services.async_call(
                    "homeassistant", service, {"entity_id": entity_id}, blocking=True
                )
        except TimeoutError:
            _LOGGER.warning(
                "%s for %s in %s did not return within %ss; continuing",
                service, entity_id, self.entry.title, SERVICE_CALL_TIMEOUT,
            )
        except Exception:  # Home Assistant reports the failing entity.
            _LOGGER.exception(
                "Unable to call %s for %s in %s", service, entity_id, self.entry.title
            )

    async def _await_group(self, entity_ids: list[str], expect_on: bool) -> list[str]:
        """Wait one shared grace period for a group; return who never answered."""
        deadline = dt_util.utcnow() + timedelta(seconds=ACTUATION_GRACE)
        pending = list(entity_ids)
        while pending:
            pending = [
                entity_id
                for entity_id in pending
                if not self._is_confirmed(
                    entity_id, expect_on, self._power_entity_for(entity_id)
                )
            ]
            remaining = (deadline - dt_util.utcnow()).total_seconds()
            if not pending or remaining <= 0:
                break
            await asyncio.sleep(min(1, remaining))
        return pending

    async def _confirm_group(
        self, entity_ids: list[str], service: str, expect_on: bool
    ) -> list[str]:
        """Confirm a whole group, resending once to whoever did not answer.

        The grace period belongs to the group, never to each entity: one
        silent light must not add its own timeout to every other light's
        wait, or the configured off time stops being a real bound.
        """
        pending = await self._await_group(entity_ids, expect_on)
        if not pending:
            return []
        _LOGGER.warning(
            "%s not confirmed for %s in %s within %ss, retrying",
            service, ", ".join(pending), self.entry.title, ACTUATION_GRACE,
        )
        for entity_id in pending:
            await self._dispatch(entity_id, service)
        pending = await self._await_group(pending, expect_on)
        if pending:
            _LOGGER.warning(
                "%s could not be confirmed for %s in %s after retry",
                service, ", ".join(pending), self.entry.title,
            )
        return pending

    def async_request_turn_on(self, **kwargs: Any) -> None:
        """Start a run without making the caller wait for the whole ramp.

        The ramp holds the caller for `interval` seconds per light plus up to two
        15 s confirmation windows -- twenty minutes for five lights at the
        maximum interval. A service call that blocks that long stalls the script
        that made it, so the sequence runs in the background and the entities
        report progress instead.
        """
        self._create_background_task(self.async_turn_on(**kwargs))

    def async_request_stop(self, interval: int | None = None) -> None:
        """Stop a run without making the caller wait for the whole sequence."""
        self._create_background_task(self.async_stop(interval=interval))

    async def async_turn_on(
        self,
        duration: int | None = None,
        source: str = SOURCE_MANUAL,
        interval: int = 0,
        schedule_id: str | None = None,
        entity_ids: list[str] | None = None,
    ) -> None:
        """Turn configured lights on sequentially for a bounded duration.

        `entity_ids` narrows the run to part of the zone; empty or None keeps
        the whole zone, so a light added later joins existing schedules.
        """
        if self._active or self._unloading:
            # Repeated clicks and overlapping schedules must never invert the
            # current run. The dedicated stop action is the only way to end it.
            # A timer that fires in the gap before unload cancels its listener
            # must not start actuating either.
            return
        max_duration = int(self.options.get(CONF_MAX_DURATION, 86400))
        seconds = duration if duration is not None else int(self.options.get(CONF_DEFAULT_DURATION, 14400))
        seconds = max(1, min(int(seconds), max_duration))
        interval = max(0, min(int(interval), MAX_SCHEDULE_INTERVAL))
        targets = self._available(self._selected_targets(entity_ids))
        if not targets:
            _LOGGER.warning("No available lights in %s", self.entry.title)
            self._schedule_next(); return
        self._active, self._source = True, source
        self._stopping = False
        self._run_interval = interval
        self._run_targets = list(targets)
        self._run_warnings = []
        self._run_schedule_id = schedule_id
        self._started_at = dt_util.utcnow()
        self._finishes_at = self._started_at + timedelta(seconds=seconds)
        self._arm_finish_timer()
        await self._close_external_records(targets)
        await self._save_runtime(immediate=True)
        self._schedule_next()

        # Arm the requested start of the shutdown sequence before ramping up.
        # The same interval is then used to turn the lights off in order.
        current_task = asyncio.current_task()
        self._ramp_task = current_task
        dispatched: list[str] = []
        try:
            for index, entity_id in enumerate(targets):
                if not self._active or self._stopping or self._unloading:
                    break
                await self._dispatch(entity_id, "turn_on")
                dispatched.append(entity_id)
                if interval and index < len(targets) - 1 and self._active and not self._stopping:
                    remaining = (self._finishes_at - dt_util.utcnow()).total_seconds()
                    if remaining <= 0:
                        break
                    await asyncio.sleep(min(interval, remaining))
            # Confirm only once the whole ramp is out, so a silent light
            # delays the report instead of the remaining lights.
            if dispatched and self._active and not self._stopping and not self._unloading:
                self._run_warnings = await self._confirm_group(
                    dispatched, "turn_on", True
                )
                if self._run_warnings:
                    self._notify()
        finally:
            if self._ramp_task is current_task:
                self._ramp_task = None

    async def _async_finish(self, _: datetime) -> None:
        await self.async_stop()

    async def async_stop(
        self, interval: int | None = None, *, schedule_next: bool = True
    ) -> None:
        """Stop an execution, joining a shutdown that is already running."""
        if not self._active:
            return
        if self._stopping:
            # Another caller already owns the shutdown. Every later caller
            # waits for it, otherwise unload returns while turn_off calls
            # are still in flight.
            stop_task = self._stop_task
            if (
                stop_task
                and stop_task is not asyncio.current_task()
                and not stop_task.done()
            ):
                await asyncio.gather(stop_task, return_exceptions=True)
            return
        self._stopping = True
        self._stop_task = asyncio.current_task()
        try:
            await self._async_stop_sequence(interval, schedule_next=schedule_next)
        finally:
            self._stop_task = None
            # A sequence that raised must not leave the zone stuck in
            # "stopping", or no later call could ever turn the lights off.
            self._stopping = False
        # Outside the finally on purpose: the replayed run must not be started
        # while this call still owns _stopping and _stop_task, or its own stop
        # would be reset from under it when this frame unwinds.
        if self._pending_start is not None:
            self._create_background_task(self._async_replay_pending_start())

    async def _async_stop_sequence(
        self, interval: int | None, *, schedule_next: bool
    ) -> None:
        """Turn every target off in order, then close and persist the run."""
        ramp_task = self._ramp_task
        current_task = asyncio.current_task()
        if ramp_task and ramp_task is not current_task and not ramp_task.done():
            ramp_task.cancel()
            await asyncio.gather(ramp_task, return_exceptions=True)
        interval = self._run_interval if interval is None else interval
        interval = max(0, min(int(interval), MAX_SCHEDULE_INTERVAL))
        if self._unloading:
            # Reloading the integration must not wait out a full stagger plus
            # two grace windows; the lights still get their turn_off, just
            # without the spacing and without waiting for confirmation.
            interval = 0
        if self._unsub_finish:
            self._unsub_finish(); self._unsub_finish = None
        self._notify()
        targets = list(self._run_targets or self.target_entity_ids)
        for index, entity_id in enumerate(targets):
            await self._dispatch(entity_id, "turn_off")
            if interval and index < len(targets) - 1:
                await asyncio.sleep(interval)
        if not self._unloading:
            self._run_warnings = list(
                dict.fromkeys(
                    [*self._run_warnings, *await self._confirm_group(targets, "turn_off", False)]
                )
            )
        started, source = self._started_at, self._source
        finished = dt_util.utcnow()
        self._history.append({"started_at": started.isoformat() if started else None, "finished_at": finished.isoformat(),
                              "duration": int((finished - started).total_seconds()) if started else None, "source": source,
                              "warnings": list(dict.fromkeys(self._run_warnings))})
        self._active = False; self._stopping = False; self._run_interval = 0
        self._run_targets = []
        self._run_warnings = []
        self._run_schedule_id = None
        self._source = None; self._started_at = None; self._finishes_at = None
        self._prune_history()
        await self._save_runtime(immediate=True)
        if schedule_next:
            self._schedule_next()

    async def _async_replay_pending_start(self) -> None:
        """Start a schedule that fired while this shutdown was running.

        The run keeps the off time the schedule asked for, counted from the
        moment it was supposed to start -- a shutdown that ate half the window
        shortens the run instead of pushing the off time an hour later. A
        schedule whose window closed entirely is reported, not started.
        """
        pending, self._pending_start = self._pending_start, None
        if pending is None or self._unloading or self._active or not self.enabled:
            return
        scheduled_at, schedule = pending
        finish = dt_util.as_utc(scheduled_at) + timedelta(
            seconds=int(schedule[CONF_SCHEDULE_DURATION])
        )
        remaining = int((finish - dt_util.utcnow()).total_seconds())
        if remaining <= 0:
            _LOGGER.warning(
                "Schedule %s in %s was skipped: the shutdown sequence lasted "
                "past its own off time",
                schedule.get(CONF_SCHEDULE_ID), self.entry.title,
            )
            return
        await self.async_turn_on(
            duration=remaining,
            source=SOURCE_SCHEDULE,
            interval=int(schedule.get(CONF_SCHEDULE_INTERVAL, 0)),
            schedule_id=schedule.get(CONF_SCHEDULE_ID),
            entity_ids=schedule.get(CONF_TARGET_ENTITY_IDS),
        )

    async def _close_external_records(self, entity_ids: list[str]) -> None:
        """Close open external records for lights this run is taking over.

        Light changes are ignored while a run is active, so a record left open
        across the run would only be closed by the light's off event afterwards
        and would report a duration that swallowed the whole run.
        """
        now = dt_util.utcnow()
        closed = False
        for item in self._history:
            if (
                item.get("source") != SOURCE_EXTERNAL
                or item.get("entity_id") not in entity_ids
                or item.get("finished_at")
            ):
                continue
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
            closed = True
        if closed:
            await self._save_history()

    async def _save_history(self) -> None:
        self._prune_history()
        await self._save_runtime()

    def _prune_history(self) -> None:
        """Remove expired history records with a single timestamp parse.

        A record whose ``started_at`` cannot be parsed is dropped rather than
        kept: retaining it meant an unreadable entry survived forever while
        still counting against the entry cap, slowly crowding out real runs.
        """
        cutoff = dt_util.utcnow() - timedelta(days=HISTORY_RETENTION_DAYS)
        retained: list[dict[str, Any]] = []
        for item in self._history:
            raw_started = item.get("started_at")
            started = (
                dt_util.parse_datetime(raw_started)
                if isinstance(raw_started, str)
                else None
            )
            if started and dt_util.as_utc(started) >= cutoff:
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
                "schedule_id": self._run_schedule_id,
            }
        await self.store.async_set(
            self.entry.entry_id,
            {"history": self._history, "active_run": active_run},
            immediate=immediate,
        )
        self._notify()

    async def async_set_enabled(self, enabled: bool) -> None:
        async with self._options_lock:
            options = {**self.options, CONF_ENABLED: enabled}
            self.hass.config_entries.async_update_entry(self.entry, options=options)
        if not enabled:
            # Pausing the zone drops a start that was waiting for the shutdown
            # to finish; otherwise it would fire right after the pause.
            self._pending_start = None
            if self._active:
                # Not awaited: the switch must answer before the stagger.
                self.async_request_stop()

    async def async_options_updated(self) -> None:
        if self._unsub_states:
            self._unsub_states()
        self._unsub_states = async_track_state_change_event(self.hass, self.target_entity_ids, self._on_light_changed)
        await self._reconcile_active_run_edit()
        self._schedule_next()

    async def _reconcile_active_run_edit(self) -> None:
        """Apply an edited off time to the schedule that is running right now.

        A schedule only sets the finish time once, at the moment it starts a
        run. Editing that same schedule afterwards used to have no effect on
        the run already in progress -- the light kept the old off time. Here
        we re-read the schedule that started the current run and, if its
        duration changed, recompute the finish time from the same start.
        """
        if not self._active or self._stopping or not self._run_schedule_id or not self._started_at:
            return
        schedule = next(
            (
                item for item in self.options.get(CONF_SCHEDULES, [])
                if item.get(CONF_SCHEDULE_ID) == self._run_schedule_id
            ),
            None,
        )
        if schedule is None:
            # The schedule was deleted mid-run. The run keeps its own off
            # time; only the now dangling back-reference goes away.
            self._run_schedule_id = None
            return
        max_duration = int(self.options.get(CONF_MAX_DURATION, 86400))
        seconds = max(1, min(int(schedule[CONF_SCHEDULE_DURATION]), max_duration))
        new_finish = self._started_at + timedelta(seconds=seconds)
        if new_finish == self._finishes_at:
            return
        self._finishes_at = new_finish
        await self._save_runtime(immediate=True)
        if self._finishes_at <= dt_util.utcnow():
            await self.async_stop()
        else:
            self._arm_finish_timer()

    def _notify(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(entry_id=self.entry.entry_id))
