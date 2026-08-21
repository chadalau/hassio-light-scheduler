"""Regression tests for the findings of the full review (REVIEW-opus5.md).

Each class names the failure it locks down, so a change that brings one back
fails here instead of in someone's living room.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import importlib
import unittest
from zoneinfo import ZoneInfo

from ha_stubs import FakeState, load_scheduler, make_scheduler

SCHEDULER = load_scheduler()
SCHEDULES = importlib.import_module("light_scheduler.schedules")
NEXT_RUN = importlib.import_module("light_scheduler.next_run")


class FakeStore:
    def __init__(self) -> None:
        self.saved: dict | None = None

    async def async_get(self, entry_id):
        return {}

    async def async_set(self, entry_id, value, *, immediate=False):
        self.saved = value


def zone(*entity_ids: str, **extra) -> dict:
    return {
        "entity_mappings": [
            {
                "name": "",
                "target_entity_id": entity_id,
                "power_entity_id": extra.get("power", {}).get(entity_id, ""),
                "power_threshold_w": extra.get("threshold", {}).get(entity_id),
            }
            for entity_id in entity_ids
        ]
    }


def schedule(**overrides) -> dict:
    base = {
        "id": "s1",
        "time": "18:30",
        "days": [0, 1, 2, 3, 4, 5, 6],
        "duration": 7200,
        "interval": 0,
        "target_entity_ids": [],
        "enabled": True,
        "warning": "",
    }
    base.update(overrides)
    return base


class PendingStartTests(unittest.TestCase):
    """A schedule firing during the shutdown must not evaporate.

    The stop sequence holds `_active` while it staggers the lights off, which
    blocks turning on, blocks extending the off time and blocks adding targets.
    With a 300 s interval that window is twenty minutes wide.
    """

    def _stopping_zone(self):
        states = {"light.a": FakeState("on")}
        scheduler, hass = make_scheduler(
            SCHEDULER, states, zone("light.a"), store=FakeStore()
        )
        now = datetime.now(timezone.utc)
        scheduler._active = True
        scheduler._stopping = True
        scheduler._run_targets = ["light.a"]
        scheduler._started_at = now
        scheduler._finishes_at = now + timedelta(seconds=5)
        return scheduler, hass, now

    def test_a_start_during_the_shutdown_is_remembered(self) -> None:
        scheduler, hass, now = self._stopping_zone()

        asyncio.run(scheduler._async_scheduled_start(now, schedule()))

        self.assertIsNotNone(scheduler._pending_start)
        self.assertEqual([], hass.calls)
        # The finish time of the run being stopped is left alone.
        self.assertEqual(now + timedelta(seconds=5), scheduler._finishes_at)

    def test_the_remembered_start_runs_once_the_zone_is_idle(self) -> None:
        scheduler, hass, now = self._stopping_zone()

        async def scenario():
            await scheduler._async_scheduled_start(now, schedule())
            scheduler._active = False
            scheduler._stopping = False
            await scheduler._async_replay_pending_start()

        asyncio.run(scenario())

        self.assertEqual([("turn_on", "light.a")], hass.calls)
        self.assertTrue(scheduler.active)
        self.assertIsNone(scheduler._pending_start)

    def test_the_replayed_run_keeps_the_original_off_time(self) -> None:
        scheduler, hass, now = self._stopping_zone()
        # The shutdown ate half of the two hour window.
        scheduled_at = now - timedelta(hours=1)

        async def scenario():
            await scheduler._async_scheduled_start(scheduled_at, schedule())
            scheduler._active = False
            scheduler._stopping = False
            await scheduler._async_replay_pending_start()

        asyncio.run(scenario())

        expected = scheduled_at + timedelta(seconds=7200)
        self.assertLess(abs((scheduler.finishes_at - expected).total_seconds()), 5)

    def test_a_window_that_already_closed_is_not_started(self) -> None:
        scheduler, hass, now = self._stopping_zone()
        scheduled_at = now - timedelta(hours=3)

        async def scenario():
            await scheduler._async_scheduled_start(scheduled_at, schedule())
            scheduler._active = False
            scheduler._stopping = False
            await scheduler._async_replay_pending_start()

        asyncio.run(scenario())

        self.assertEqual([], hass.calls)
        self.assertFalse(scheduler.active)

    def test_unloading_drops_the_remembered_start(self) -> None:
        scheduler, hass, now = self._stopping_zone()

        async def scenario():
            scheduler._unloading = True
            await scheduler._async_scheduled_start(now, schedule())

        asyncio.run(scenario())

        self.assertIsNone(scheduler._pending_start)

    def test_a_paused_zone_does_not_replay(self) -> None:
        scheduler, hass, now = self._stopping_zone()
        scheduler.entry.options = {**scheduler.entry.options, "enabled": False}

        async def scenario():
            scheduler._pending_start = (now, schedule())
            scheduler._active = False
            scheduler._stopping = False
            await scheduler._async_replay_pending_start()

        asyncio.run(scenario())

        self.assertEqual([], hass.calls)


class PruneScheduleTargetsTests(unittest.TestCase):
    """A selection the zone no longer controls resolves to nothing.

    It used to stay untouched, so the schedule never ran again while the card
    kept showing it as scheduled.
    """

    def test_a_light_that_left_the_zone_is_dropped(self) -> None:
        schedules, disabled = SCHEDULES.prune_schedule_targets(
            [schedule(target_entity_ids=["light.a", "light.gone"])], ["light.a"]
        )

        self.assertEqual(["light.a"], schedules[0]["target_entity_ids"])
        self.assertTrue(schedules[0]["enabled"])
        self.assertEqual([], disabled)

    def test_an_emptied_selection_disables_the_schedule(self) -> None:
        schedules, disabled = SCHEDULES.prune_schedule_targets(
            [schedule(target_entity_ids=["light.gone"])], ["light.a"]
        )

        self.assertEqual([], schedules[0]["target_entity_ids"])
        self.assertFalse(schedules[0]["enabled"])
        self.assertEqual("targets_removed", schedules[0]["warning"])
        self.assertEqual(["s1"], disabled)

    def test_an_empty_selection_still_means_the_whole_zone(self) -> None:
        original = schedule(target_entity_ids=[])

        schedules, disabled = SCHEDULES.prune_schedule_targets([original], ["light.a"])

        self.assertTrue(schedules[0]["enabled"])
        self.assertEqual("", schedules[0]["warning"])
        self.assertEqual([], disabled)

    def test_an_intact_selection_is_untouched(self) -> None:
        schedules, disabled = SCHEDULES.prune_schedule_targets(
            [schedule(target_entity_ids=["light.a"])], ["light.a", "light.b"]
        )

        self.assertEqual(["light.a"], schedules[0]["target_entity_ids"])
        self.assertEqual([], disabled)


class AmbiguousTimeTests(unittest.TestCase):
    """A local hour that happens twice must be flagged, not resolved silently."""

    def _timezone(self):
        try:
            return ZoneInfo("America/New_York")
        except Exception:  # pragma: no cover - depends on the host
            self.skipTest("Timezone database is not installed")

    def test_a_repeated_hour_is_detected(self) -> None:
        local = self._timezone()
        self.assertTrue(
            NEXT_RUN.is_ambiguous(datetime(2026, 11, 1, 1, 30, tzinfo=local))
        )

    def test_a_normal_hour_is_not_ambiguous(self) -> None:
        local = self._timezone()
        self.assertFalse(
            NEXT_RUN.is_ambiguous(datetime(2026, 11, 1, 4, 30, tzinfo=local))
        )

    def test_the_schedule_on_a_repeated_hour_is_reported(self) -> None:
        local = self._timezone()
        now = datetime(2026, 10, 31, 12, 0, tzinfo=local)
        ambiguous = schedule(id="dst", time="01:30", days=[6])
        normal = schedule(id="ok", time="04:30", days=[6])

        self.assertEqual(
            ["dst"], NEXT_RUN.ambiguous_schedule_ids([ambiguous, normal], now)
        )

    def test_the_first_occurrence_is_the_one_used(self) -> None:
        local = self._timezone()
        now = datetime(2026, 10, 31, 12, 0, tzinfo=local)

        instant, _ = NEXT_RUN.find_next_run(
            [schedule(id="dst", time="01:30", days=[6])], now
        )

        self.assertEqual(0, instant.fold)
        self.assertEqual(timedelta(hours=-4), instant.utcoffset())


class PowerThresholdTests(unittest.TestCase):
    """A sub-watt light would never confirm against a fixed global threshold."""

    def test_the_default_threshold_rejects_a_sub_watt_reading(self) -> None:
        states = {
            "light.a": FakeState("on"),
            "sensor.a": FakeState("0.5", device_class="power"),
        }
        scheduler, _ = make_scheduler(
            SCHEDULER, states, zone("light.a", power={"light.a": "sensor.a"})
        )

        self.assertFalse(scheduler._is_confirmed("light.a", True, "sensor.a"))

    def test_a_lowered_threshold_accepts_it(self) -> None:
        states = {
            "light.a": FakeState("on"),
            "sensor.a": FakeState("0.5", device_class="power"),
        }
        scheduler, _ = make_scheduler(
            SCHEDULER,
            states,
            zone(
                "light.a",
                power={"light.a": "sensor.a"},
                threshold={"light.a": 0.2},
            ),
        )

        self.assertTrue(scheduler._is_confirmed("light.a", True, "sensor.a"))

    def test_an_unusable_threshold_falls_back_to_the_default(self) -> None:
        states = {
            "light.a": FakeState("on"),
            "sensor.a": FakeState("5", device_class="power"),
        }
        scheduler, _ = make_scheduler(
            SCHEDULER,
            states,
            zone(
                "light.a",
                power={"light.a": "sensor.a"},
                threshold={"light.a": "nao e um numero"},
            ),
        )

        self.assertEqual(1.0, scheduler._power_threshold_for("light.a"))
        self.assertTrue(scheduler._is_confirmed("light.a", True, "sensor.a"))


class NonBlockingServiceTests(unittest.TestCase):
    """A service must not hold its caller for the whole ramp or stagger."""

    def test_turn_on_returns_before_the_ramp_runs(self) -> None:
        states = {entity: FakeState("on") for entity in ("light.a", "light.b")}
        scheduler, hass = make_scheduler(
            SCHEDULER, states, zone("light.a", "light.b"), store=FakeStore()
        )

        async def scenario():
            scheduler.async_request_turn_on(duration=60)
            # The request has returned and nothing has been dispatched yet.
            queued = list(hass.calls)
            await asyncio.gather(*hass.tasks)
            return queued, list(hass.calls)

        queued, after = asyncio.run(scenario())

        self.assertEqual([], queued)
        self.assertEqual([("turn_on", "light.a"), ("turn_on", "light.b")], after)

    def test_stop_returns_before_the_sequence_runs(self) -> None:
        states = {"light.a": FakeState("off")}
        scheduler, hass = make_scheduler(
            SCHEDULER, states, zone("light.a"), store=FakeStore()
        )
        now = datetime.now(timezone.utc)
        scheduler._active = True
        scheduler._run_targets = ["light.a"]
        scheduler._started_at = now
        scheduler._finishes_at = now + timedelta(seconds=60)

        async def scenario():
            scheduler.async_request_stop()
            queued = list(hass.calls)
            await asyncio.gather(*hass.tasks)
            return queued, list(hass.calls)

        queued, after = asyncio.run(scenario())

        self.assertEqual([], queued)
        self.assertEqual([("turn_off", "light.a")], after)
        self.assertFalse(scheduler.active)


class BoundedUnloadTests(unittest.TestCase):
    """Unload must not wait out a full stagger plus two grace windows."""

    def test_the_stagger_is_skipped_while_unloading(self) -> None:
        entities = ["light.a", "light.b", "light.c"]
        states = {entity: FakeState("on") for entity in entities}
        scheduler, hass = make_scheduler(
            SCHEDULER, states, zone(*entities), store=FakeStore()
        )
        now = datetime.now(timezone.utc)
        scheduler._active = True
        scheduler._run_targets = list(entities)
        scheduler._run_interval = 300
        scheduler._started_at = now
        scheduler._finishes_at = now + timedelta(seconds=600)

        async def scenario():
            # Three lights at the maximum interval would sleep for ten minutes.
            await asyncio.wait_for(scheduler.async_unload(), timeout=5)

        asyncio.run(scenario())

        self.assertEqual([("turn_off", entity) for entity in entities], hass.calls)
        self.assertFalse(scheduler.active)


if __name__ == "__main__":
    unittest.main()
