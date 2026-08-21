"""Regression tests for grouped actuation confirmation.

The scheduler is loaded against the shared Home Assistant stubs so the
timing guarantees can be asserted without a running Home Assistant.
"""

from __future__ import annotations

import asyncio
import time
import unittest
from typing import ClassVar

from ha_stubs import FakeState, load_scheduler, make_scheduler

SCHEDULER = load_scheduler()
GRACE = 0.1


class ConfirmGroupTests(unittest.TestCase):
    """The grace period must belong to the group, never to each entity."""

    def setUp(self) -> None:
        self._original_grace = SCHEDULER.ACTUATION_GRACE
        SCHEDULER.ACTUATION_GRACE = GRACE

    def tearDown(self) -> None:
        SCHEDULER.ACTUATION_GRACE = self._original_grace

    def test_silent_group_waits_once_not_once_per_entity(self) -> None:
        entities = [f"light.sala_{index}" for index in range(6)]
        # Every light stays off while we ask it to turn on: nothing confirms.
        states = {entity: FakeState("off") for entity in entities}
        scheduler, hass = make_scheduler(SCHEDULER, states)

        started = time.monotonic()
        pending = asyncio.run(scheduler._confirm_group(entities, "turn_on", True))
        elapsed = time.monotonic() - started

        self.assertEqual(entities, pending)
        # One retry round for the whole group: ~2 grace periods total.
        # Per-entity waiting would cost len(entities) * 2 * GRACE = 1.2s.
        self.assertLess(elapsed, 6 * GRACE)
        # Only the retry dispatches happen here; the first send is the caller's.
        self.assertEqual([("turn_on", entity) for entity in entities], hass.calls)

    def test_confirmed_group_returns_immediately(self) -> None:
        entities = ["light.a", "light.b"]
        states = {entity: FakeState("on") for entity in entities}
        scheduler, hass = make_scheduler(SCHEDULER, states)

        started = time.monotonic()
        pending = asyncio.run(scheduler._confirm_group(entities, "turn_on", True))
        elapsed = time.monotonic() - started

        self.assertEqual([], pending)
        self.assertLess(elapsed, GRACE)
        self.assertEqual([], hass.calls)

    def test_only_unconfirmed_entities_are_retried(self) -> None:
        states = {"light.ok": FakeState("on"), "light.mudo": FakeState("off")}
        scheduler, hass = make_scheduler(SCHEDULER, states)

        pending = asyncio.run(
            scheduler._confirm_group(["light.ok", "light.mudo"], "turn_on", True)
        )

        self.assertEqual(["light.mudo"], pending)
        self.assertEqual([("turn_on", "light.mudo")], hass.calls)


class PowerConfirmationTests(unittest.TestCase):
    """A paired power sensor decides only when it is a real power reading."""

    OPTIONS: ClassVar[dict] = {
        "entity_mappings": [
            {
                "name": "",
                "target_entity_id": "light.sala",
                "power_entity_id": "sensor.sala_w",
            }
        ]
    }

    def test_power_below_threshold_refuses_to_confirm_on(self) -> None:
        states = {
            "light.sala": FakeState("on"),
            "sensor.sala_w": FakeState("0.2", device_class="power"),
        }
        scheduler, _ = make_scheduler(SCHEDULER, states, self.OPTIONS)

        self.assertFalse(
            scheduler._is_confirmed("light.sala", True, "sensor.sala_w")
        )

    def test_power_above_threshold_confirms_on(self) -> None:
        states = {
            "light.sala": FakeState("on"),
            "sensor.sala_w": FakeState("42", device_class="power"),
        }
        scheduler, _ = make_scheduler(SCHEDULER, states, self.OPTIONS)

        self.assertTrue(scheduler._is_confirmed("light.sala", True, "sensor.sala_w"))

    def test_non_power_sensor_is_ignored_instead_of_blocking(self) -> None:
        # A temperature paired by mistake would read as a permanent load and
        # make every turn_off fail to confirm; it must be ignored instead.
        states = {
            "light.sala": FakeState("off"),
            "sensor.sala_w": FakeState("22", device_class="temperature"),
        }
        scheduler, _ = make_scheduler(SCHEDULER, states, self.OPTIONS)

        self.assertTrue(scheduler._is_confirmed("light.sala", False, "sensor.sala_w"))

    def test_unavailable_power_sensor_falls_back_to_state(self) -> None:
        states = {
            "light.sala": FakeState("on"),
            "sensor.sala_w": FakeState("unavailable", device_class="power"),
        }
        scheduler, _ = make_scheduler(SCHEDULER, states, self.OPTIONS)

        self.assertTrue(scheduler._is_confirmed("light.sala", True, "sensor.sala_w"))


if __name__ == "__main__":
    unittest.main()
