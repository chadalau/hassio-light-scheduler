"""Regression tests for the issues the independent reviews found.

Each test names the failure it locks down, so a future change that
reintroduces one fails here rather than in someone's living room.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta

from ha_stubs import FakeState, load_scheduler, make_scheduler

SCHEDULER = load_scheduler()


class FakeStore:
    """Captures what the scheduler persists, without touching disk."""

    def __init__(self) -> None:
        self.saved: dict | None = None

    async def async_get(self, entry_id):
        return {}

    async def async_set(self, entry_id, value, *, immediate=False):
        self.saved = value


def zone(*entity_ids: str) -> dict:
    return {
        "entity_mappings": [
            {"name": "", "target_entity_id": entity_id, "power_entity_id": ""}
            for entity_id in entity_ids
        ]
    }


class SelectionTests(unittest.TestCase):
    """A schedule's own light selection must be honoured and bounded."""

    def test_empty_selection_means_the_whole_zone(self) -> None:
        scheduler, _ = make_scheduler(SCHEDULER, {}, zone("light.a", "light.b"))
        self.assertEqual(["light.a", "light.b"], scheduler._selected_targets(None))
        self.assertEqual(["light.a", "light.b"], scheduler._selected_targets([]))

    def test_selection_keeps_the_zone_order(self) -> None:
        scheduler, _ = make_scheduler(
            SCHEDULER, {}, zone("light.a", "light.b", "light.c")
        )
        self.assertEqual(
            ["light.a", "light.c"],
            scheduler._selected_targets(["light.c", "light.a"]),
        )

    def test_entities_outside_the_zone_are_ignored(self) -> None:
        scheduler, _ = make_scheduler(SCHEDULER, {}, zone("light.a"))
        self.assertEqual(["light.a"], scheduler._selected_targets(["light.a", "light.x"]))

    def test_unavailable_targets_are_dropped(self) -> None:
        states = {
            "light.a": FakeState("off"),
            "light.b": FakeState("unavailable"),
            "light.c": FakeState("unknown"),
        }
        scheduler, _ = make_scheduler(SCHEDULER, states, zone("light.a", "light.b", "light.c"))
        self.assertEqual(["light.a"], scheduler._available(["light.a", "light.b", "light.c"]))


class ExtendTargetsTests(unittest.TestCase):
    """A wider schedule firing over a narrower run must light what it adds.

    Extending only the off time left the added lights dark for the whole run
    and never turned them off at the end either.
    """

    def setUp(self) -> None:
        self.states = {
            "light.a": FakeState("on"),
            "light.b": FakeState("on"),
            "light.c": FakeState("on"),
        }
        self.scheduler, self.hass = make_scheduler(
            SCHEDULER, self.states, zone("light.a", "light.b", "light.c")
        )
        self.scheduler.store = FakeStore()
        self.scheduler._active = True
        self.scheduler._run_targets = ["light.a", "light.b"]
        self.scheduler._started_at = datetime.now(UTC)
        self.scheduler._finishes_at = self.scheduler._started_at + timedelta(hours=1)

    def test_added_light_is_turned_on_and_joins_the_run(self) -> None:
        asyncio.run(
            self.scheduler._async_extend_targets(
                {"target_entity_ids": ["light.a", "light.b", "light.c"]}
            )
        )
        self.assertIn(("turn_on", "light.c"), self.hass.calls)
        self.assertEqual(
            ["light.a", "light.b", "light.c"], self.scheduler._run_targets
        )

    def test_already_running_lights_are_not_re_dispatched(self) -> None:
        asyncio.run(
            self.scheduler._async_extend_targets(
                {"target_entity_ids": ["light.a", "light.b", "light.c"]}
            )
        )
        self.assertEqual([("turn_on", "light.c")], self.hass.calls)

    def test_a_narrower_schedule_adds_nothing(self) -> None:
        asyncio.run(
            self.scheduler._async_extend_targets({"target_entity_ids": ["light.a"]})
        )
        self.assertEqual([], self.hass.calls)
        self.assertEqual(["light.a", "light.b"], self.scheduler._run_targets)

    def test_a_whole_zone_schedule_adds_the_missing_light(self) -> None:
        asyncio.run(self.scheduler._async_extend_targets({}))
        self.assertIn("light.c", self.scheduler._run_targets)

    def test_nothing_is_added_while_stopping(self) -> None:
        self.scheduler._stopping = True
        asyncio.run(self.scheduler._async_extend_targets({}))
        self.assertEqual([], self.hass.calls)
        self.assertEqual(["light.a", "light.b"], self.scheduler._run_targets)

    def test_an_unavailable_addition_is_skipped(self) -> None:
        self.states["light.c"] = FakeState("unavailable")
        asyncio.run(self.scheduler._async_extend_targets({}))
        self.assertEqual([], self.hass.calls)
        self.assertNotIn("light.c", self.scheduler._run_targets)


class RestoreTargetsTests(unittest.TestCase):
    """A light dropped from the zone must not be waited on at stop.

    Confirming an entity that can never answer costs a full grace period plus
    a retry, delaying the shutdown of everything else.
    """

    def _restore(self, stored_targets, configured):
        scheduler, _ = make_scheduler(SCHEDULER, {}, zone(*configured))
        started = datetime.now(UTC)
        scheduler._restore_active_run(
            {
                "started_at": started.isoformat(),
                "finishes_at": (started + timedelta(hours=1)).isoformat(),
                "source": "schedule",
                "targets": stored_targets,
            }
        )
        return scheduler

    def test_removed_targets_are_discarded(self) -> None:
        scheduler = self._restore(
            ["light.a", "light.gone"], ["light.a", "light.b"]
        )
        self.assertEqual(["light.a"], scheduler._run_targets)

    def test_all_targets_removed_falls_back_to_the_zone(self) -> None:
        scheduler = self._restore(["light.gone"], ["light.a", "light.b"])
        self.assertEqual(["light.a", "light.b"], scheduler._run_targets)

    def test_a_run_without_stored_targets_uses_the_zone(self) -> None:
        scheduler = self._restore(None, ["light.a"])
        self.assertEqual(["light.a"], scheduler._run_targets)


class PruneHistoryTests(unittest.TestCase):
    """An unreadable record used to survive forever, crowding out real runs."""

    def _prune(self, history):
        scheduler, _ = make_scheduler(SCHEDULER, {}, zone("light.a"))
        scheduler._history = history
        scheduler._prune_history()
        return scheduler._history

    def test_a_record_with_an_unparsable_timestamp_is_dropped(self) -> None:
        kept = self._prune(
            [
                {"started_at": "not a date", "source": "schedule"},
                {"started_at": _now_iso(), "source": "schedule"},
            ]
        )
        self.assertEqual(1, len(kept))

    def test_a_record_without_a_timestamp_is_dropped(self) -> None:
        self.assertEqual([], self._prune([{"source": "schedule"}]))

    def test_expired_records_are_dropped(self) -> None:
        self.assertEqual([], self._prune([{"started_at": _days_ago_iso(40)}]))

    def test_recent_records_survive(self) -> None:
        self.assertEqual(1, len(self._prune([{"started_at": _now_iso()}])))


class LastFinishedAtTests(unittest.TestCase):
    """The inactive header timeline starts at the last scheduler shutdown."""

    def test_returns_latest_owned_run_completion(self) -> None:
        scheduler, _ = make_scheduler(SCHEDULER, {}, zone("light.a"))
        older = datetime.now(UTC) - timedelta(hours=3)
        latest = datetime.now(UTC) - timedelta(minutes=1)
        scheduler._history = [
            {"finished_at": older.isoformat(), "source": "schedule"},
            {"finished_at": latest.isoformat(), "source": "manual"},
        ]

        self.assertEqual(latest, scheduler.last_finished_at)

    def test_ignores_external_activity(self) -> None:
        scheduler, _ = make_scheduler(SCHEDULER, {}, zone("light.a"))
        owned = datetime.now(UTC) - timedelta(hours=2)
        external = datetime.now(UTC) - timedelta(minutes=1)
        scheduler._history = [
            {"finished_at": owned.isoformat(), "source": "schedule"},
            {"finished_at": external.isoformat(), "source": "external"},
        ]

        self.assertEqual(owned, scheduler.last_finished_at)

    def test_returns_none_without_a_valid_owned_completion(self) -> None:
        scheduler, _ = make_scheduler(SCHEDULER, {}, zone("light.a"))
        scheduler._history = [
            {"finished_at": "invalid", "source": "schedule"},
            {"finished_at": _now_iso(), "source": "external"},
        ]

        self.assertIsNone(scheduler.last_finished_at)


class ExternalRecordTests(unittest.TestCase):
    """An external record left open across a run reported a bogus duration.

    Light changes are ignored while a run is active, so the record would only
    be closed by the off event afterwards and swallow the whole run.
    """

    def test_an_open_record_is_closed_when_the_run_takes_over(self) -> None:
        scheduler, _ = make_scheduler(SCHEDULER, {}, zone("light.a"))
        scheduler.store = FakeStore()
        started = datetime.now(UTC) - timedelta(minutes=30)
        scheduler._history = [
            {
                "started_at": started.isoformat(),
                "finished_at": None,
                "duration": None,
                "source": "external",
                "entity_id": "light.a",
            }
        ]

        asyncio.run(scheduler._close_external_records(["light.a"]))

        record = scheduler._history[0]
        self.assertIsNotNone(record["finished_at"])
        # About half an hour, not the run's length on top of it.
        self.assertGreater(record["duration"], 1700)
        self.assertLess(record["duration"], 1900)

    def test_records_for_other_lights_are_left_alone(self) -> None:
        scheduler, _ = make_scheduler(SCHEDULER, {}, zone("light.a", "light.b"))
        scheduler.store = FakeStore()
        scheduler._history = [
            {
                "started_at": _now_iso(),
                "finished_at": None,
                "duration": None,
                "source": "external",
                "entity_id": "light.b",
            }
        ]

        asyncio.run(scheduler._close_external_records(["light.a"]))

        self.assertIsNone(scheduler._history[0]["finished_at"])

    def test_an_already_closed_record_is_untouched(self) -> None:
        scheduler, _ = make_scheduler(SCHEDULER, {}, zone("light.a"))
        scheduler.store = FakeStore()
        scheduler._history = [
            {
                "started_at": _now_iso(),
                "finished_at": "2026-08-16T10:00:00+00:00",
                "duration": 60,
                "source": "external",
                "entity_id": "light.a",
            }
        ]

        asyncio.run(scheduler._close_external_records(["light.a"]))

        self.assertEqual(60, scheduler._history[0]["duration"])


class DispatchTimeoutTests(unittest.TestCase):
    """An integration that never returns must not hang stop or unload."""

    def test_a_hanging_service_call_is_abandoned(self) -> None:

        from ha_stubs import FakeHass

        original = SCHEDULER.SERVICE_CALL_TIMEOUT
        SCHEDULER.SERVICE_CALL_TIMEOUT = 0.05
        try:
            scheduler, _ = make_scheduler(SCHEDULER, {}, zone("light.a"))
            hanging = FakeHass({"light.a": FakeState("off")}, hang=True)
            scheduler.hass = hanging

            async def scenario():
                await asyncio.wait_for(
                    scheduler._dispatch("light.a", "turn_off"), timeout=2
                )

            # Returns instead of hanging: wait_for would raise on a real hang.
            asyncio.run(scenario())
            self.assertEqual([("turn_off", "light.a")], hanging.calls)
        finally:
            SCHEDULER.SERVICE_CALL_TIMEOUT = original


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _days_ago_iso(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


if __name__ == "__main__":
    unittest.main()
