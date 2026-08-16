"""Regression tests for grouped actuation confirmation.

The scheduler is loaded with minimal Home Assistant stubs so the timing
guarantees can be asserted without a running Home Assistant.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
import time
import types
import unittest


PACKAGE_DIR = Path(__file__).parents[1] / "custom_components" / "light_scheduler"


def _install_homeassistant_stubs() -> None:
    """Register the small slice of Home Assistant the scheduler imports."""
    if "homeassistant" in sys.modules:
        return

    def module(name: str, **attributes: object) -> types.ModuleType:
        created = types.ModuleType(name)
        for key, value in attributes.items():
            setattr(created, key, value)
        sys.modules[name] = created
        return created

    module("homeassistant")
    module("homeassistant.config_entries", ConfigEntry=object)
    module(
        "homeassistant.const",
        EVENT_HOMEASSISTANT_STARTED="homeassistant_started",
        STATE_OFF="off",
        STATE_ON="on",
        STATE_UNAVAILABLE="unavailable",
        STATE_UNKNOWN="unknown",
        Platform=types.SimpleNamespace(
            SWITCH="switch", SENSOR="sensor", BINARY_SENSOR="binary_sensor"
        ),
    )
    module(
        "homeassistant.core",
        CoreState=types.SimpleNamespace(running="running"),
        Event=object,
        HomeAssistant=object,
        State=object,
        callback=lambda func: func,
    )
    module("homeassistant.helpers")
    module("homeassistant.helpers.dispatcher", async_dispatcher_send=lambda *a, **k: None)
    module(
        "homeassistant.helpers.event",
        async_track_point_in_time=lambda *a, **k: (lambda: None),
        async_track_state_change_event=lambda *a, **k: (lambda: None),
    )
    module("homeassistant.helpers.storage", Store=object)
    module("homeassistant.util")
    module(
        "homeassistant.util.dt",
        utcnow=lambda: datetime.now(timezone.utc),
        now=lambda: datetime.now(timezone.utc),
        parse_datetime=lambda value: None,
        as_utc=lambda value: value,
    )


def _load_scheduler() -> types.ModuleType:
    """Import the scheduler module without executing the package __init__."""
    _install_homeassistant_stubs()
    if "light_scheduler" not in sys.modules:
        package = types.ModuleType("light_scheduler")
        package.__path__ = [str(PACKAGE_DIR)]
        sys.modules["light_scheduler"] = package
    import light_scheduler.scheduler as scheduler_module

    return scheduler_module


SCHEDULER = _load_scheduler()
GRACE = 0.1


class FakeState:
    def __init__(self, state: str, **attributes: object) -> None:
        self.state = state
        self.attributes = dict(attributes)


class FakeHass:
    """Records service calls and serves whatever states the test sets."""

    def __init__(self, states: dict[str, FakeState]) -> None:
        self.states = types.SimpleNamespace(get=states.get)
        self.calls: list[tuple[str, str]] = []
        self.services = types.SimpleNamespace(async_call=self._async_call)

    async def _async_call(self, domain, service, data, blocking=False):
        self.calls.append((service, data["entity_id"]))


def _make_scheduler(states, options=None):
    entry = types.SimpleNamespace(
        entry_id="entry", title="Sala", domain="light_scheduler",
        options=options or {},
    )
    hass = FakeHass(states)
    scheduler = SCHEDULER.LightScheduler(hass, entry, store=None)
    return scheduler, hass


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
        scheduler, hass = _make_scheduler(states)

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
        scheduler, hass = _make_scheduler(states)

        started = time.monotonic()
        pending = asyncio.run(scheduler._confirm_group(entities, "turn_on", True))
        elapsed = time.monotonic() - started

        self.assertEqual([], pending)
        self.assertLess(elapsed, GRACE)
        self.assertEqual([], hass.calls)

    def test_only_unconfirmed_entities_are_retried(self) -> None:
        states = {"light.ok": FakeState("on"), "light.mudo": FakeState("off")}
        scheduler, hass = _make_scheduler(states)

        pending = asyncio.run(
            scheduler._confirm_group(["light.ok", "light.mudo"], "turn_on", True)
        )

        self.assertEqual(["light.mudo"], pending)
        self.assertEqual([("turn_on", "light.mudo")], hass.calls)


class PowerConfirmationTests(unittest.TestCase):
    """A paired power sensor decides only when it is a real power reading."""

    OPTIONS = {
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
        scheduler, _ = _make_scheduler(states, self.OPTIONS)

        self.assertFalse(
            scheduler._is_confirmed("light.sala", True, "sensor.sala_w")
        )

    def test_power_above_threshold_confirms_on(self) -> None:
        states = {
            "light.sala": FakeState("on"),
            "sensor.sala_w": FakeState("42", device_class="power"),
        }
        scheduler, _ = _make_scheduler(states, self.OPTIONS)

        self.assertTrue(scheduler._is_confirmed("light.sala", True, "sensor.sala_w"))

    def test_non_power_sensor_is_ignored_instead_of_blocking(self) -> None:
        # A temperature paired by mistake would read as a permanent load and
        # make every turn_off fail to confirm; it must be ignored instead.
        states = {
            "light.sala": FakeState("off"),
            "sensor.sala_w": FakeState("22", device_class="temperature"),
        }
        scheduler, _ = _make_scheduler(states, self.OPTIONS)

        self.assertTrue(scheduler._is_confirmed("light.sala", False, "sensor.sala_w"))

    def test_unavailable_power_sensor_falls_back_to_state(self) -> None:
        states = {
            "light.sala": FakeState("on"),
            "sensor.sala_w": FakeState("unavailable", device_class="power"),
        }
        scheduler, _ = _make_scheduler(states, self.OPTIONS)

        self.assertTrue(scheduler._is_confirmed("light.sala", True, "sensor.sala_w"))


if __name__ == "__main__":
    unittest.main()
