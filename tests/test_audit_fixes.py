"""Regression tests for the findings of the 0.8.0 audit.

A1 is the reason the escaping test below scans the whole card instead of
checking one line: the hole was a single attribute that stopped being a literal,
and nothing would have noticed the next one.
"""

from __future__ import annotations

import asyncio
import re
import types
import unittest
from datetime import UTC, datetime
from pathlib import Path

from ha_stubs import FakeState, load_scheduler, make_scheduler

SCHEDULER = load_scheduler()
CARD = (
    Path(__file__).parents[1]
    / "custom_components"
    / "light_scheduler"
    / "frontend"
    / "light-schedule-card.js"
)

# A double-quoted HTML attribute value, on one line, that interpolates anything.
ATTRIBUTE_WITH_INTERPOLATION = re.compile(r'="[^"\n]*?\$\{[^"\n]*?"')

# The only attributes allowed to interpolate without _escape(): loop counters
# and ternaries whose branches are string constants. A ternary is truncated at
# its first inner quote by the pattern above, which is enough to name the
# attribute and the variable driving it. Every entry is matched exactly, so
# changing one of these attributes fails this test until it is reviewed.
ALLOWED_RAW_ATTRIBUTES = frozenset(
    {
        '="${index}"',              # day checkbox, index of a literal array
        '="${field}"',              # "mapping_target" or "mapping_power"
        '="${kind}"',               # "target" or "power"
        '="${label} da entrada ${index + 1}"',
        '="Nome da entrada ${index + 1}"',
        '="Remover entrada ${index + 1}"',
        '="status-chip ${enabled ? "',
        '="light-tile ${isOn ? "',
        '="schedule-row ${enabled ? "',
        '="${hasPower ? "',
    }
)


class FakeStore:
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


class CardEscapingTests(unittest.TestCase):
    """Every HTML attribute built from data must be escaped.

    A friendly_name is text an integration, an MQTT discovery payload or another
    user chose. Interpolated raw into an attribute it closes the quote and the
    rest becomes live markup, executing in the Home Assistant origin of whoever
    opens the dashboard.
    """

    def test_no_attribute_interpolates_unescaped_data(self) -> None:
        source = CARD.read_text(encoding="utf-8")
        offenders = sorted(
            {
                match.group(0)
                for match in re.finditer(ATTRIBUTE_WITH_INTERPOLATION, source)
                if "_escape(" not in match.group(0)
                and match.group(0) not in ALLOWED_RAW_ATTRIBUTES
            }
        )
        self.assertEqual([], offenders, "unescaped data in an HTML attribute")

    def test_the_resolved_sensor_placeholder_is_escaped(self) -> None:
        source = CARD.read_text(encoding="utf-8")
        self.assertIn('placeholder="${this._escape(placeholder)}"', source)

    def test_escape_neutralizes_an_attribute_breakout(self) -> None:
        # The exact payload used to prove the hole was real.
        payload = '"><img src=x onerror="alert(1)"><span y="'
        escaped = (
            payload.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#039;")
        )
        self.assertNotIn("<", escaped)
        self.assertNotIn('"', escaped)


class CardZoneRenameTests(unittest.TestCase):
    """The settings dialog must send an edited card name through the service."""

    def test_zone_dialog_exposes_a_bounded_name_field(self) -> None:
        source = CARD.read_text(encoding="utf-8")

        self.assertIn('name="zone_name"', source)
        self.assertIn('maxlength="64"', source)
        self.assertIn('value="${this._escape(zoneName)}"', source)

    def test_save_adds_the_name_only_when_it_changed(self) -> None:
        source = CARD.read_text(encoding="utf-8")

        self.assertIn('if (zoneName !== currentName) data.name = zoneName;', source)


class ExternalRecordRaceTests(unittest.TestCase):
    """A light event queued while idle must not open a record inside a run.

    The callback checks _active before queuing, but a run can start before the
    queued task gets its turn. Off events are ignored while active, so such a
    record was never closed: it reported a duration that swallowed the run and
    survived until the 30 day retention pruned it.
    """

    def test_a_run_starting_first_cancels_the_queued_record(self) -> None:
        states = {"light.a": FakeState("on")}
        scheduler, hass = make_scheduler(
            SCHEDULER, states, zone("light.a"), store=FakeStore()
        )

        async def scenario():
            scheduler._on_light_changed(
                types.SimpleNamespace(
                    data={"entity_id": "light.a", "new_state": FakeState("on")}
                )
            )
            # The run wins the race: the queued task has not run yet.
            await scheduler.async_turn_on(duration=60)
            await asyncio.gather(*hass.tasks, return_exceptions=True)

        asyncio.run(scenario())

        self.assertTrue(scheduler.active)
        self.assertEqual([], scheduler.history)

    def test_unloading_also_cancels_the_queued_record(self) -> None:
        states = {"light.a": FakeState("on")}
        scheduler, hass = make_scheduler(
            SCHEDULER, states, zone("light.a"), store=FakeStore()
        )

        async def scenario():
            scheduler._on_light_changed(
                types.SimpleNamespace(
                    data={"entity_id": "light.a", "new_state": FakeState("on")}
                )
            )
            scheduler._unloading = True
            await asyncio.gather(*hass.tasks, return_exceptions=True)

        asyncio.run(scenario())

        self.assertEqual([], scheduler.history)

    def test_an_idle_zone_still_records_the_event(self) -> None:
        states = {"light.a": FakeState("on")}
        scheduler, hass = make_scheduler(
            SCHEDULER, states, zone("light.a"), store=FakeStore()
        )

        async def scenario():
            scheduler._on_light_changed(
                types.SimpleNamespace(
                    data={"entity_id": "light.a", "new_state": FakeState("on")}
                )
            )
            await asyncio.gather(*hass.tasks, return_exceptions=True)

        asyncio.run(scenario())

        self.assertEqual(1, len(scheduler.history))
        self.assertEqual("external", scheduler.history[0]["source"])


class ZoneOwnershipTests(unittest.TestCase):
    """One light, one zone.

    Two zones driving the same light fight silently: the first run to end sends
    turn_off and the second keeps reporting itself as on, because a zone ignores
    state changes on its own targets while active.
    """

    def setUp(self) -> None:
        self.zones = importlib_zones()

    def test_a_light_owned_by_another_zone_is_reported(self) -> None:
        zones = [("a", ["light.x", "light.y"]), ("b", ["light.z"])]

        self.assertEqual(
            ["light.y"], self.zones.foreign_entities(zones, ["light.y", "light.new"])
        )

    def test_a_zone_does_not_collide_with_itself(self) -> None:
        zones = [("a", ["light.x"])]

        self.assertEqual(
            [], self.zones.foreign_entities(zones, ["light.x"], skip_entry_id="a")
        )

    def test_only_the_overlap_an_edit_adds_is_refused(self) -> None:
        zones = [("a", ["light.shared"]), ("b", ["light.shared", "light.own"])]

        # b already shares light.shared; keeping it is not a new offence.
        self.assertEqual(
            [],
            self.zones.newly_shared_entities(
                zones, ["light.shared", "light.own"], ["light.shared"], "b"
            ),
        )

    def test_taking_a_new_light_from_another_zone_is_refused(self) -> None:
        zones = [("a", ["light.x", "light.y"]), ("b", ["light.own"])]

        self.assertEqual(
            ["light.y"],
            self.zones.newly_shared_entities(
                zones, ["light.own"], ["light.own", "light.y"], "b"
            ),
        )

    def test_zone_targets_reads_the_config_entries(self) -> None:
        entry = types.SimpleNamespace(
            entry_id="a", options={"target_entity_ids": ["light.x"]}
        )

        self.assertEqual([("a", ["light.x"])], self.zones.zone_targets([entry]))


def importlib_zones():
    import importlib

    return importlib.import_module("light_scheduler.zones")


class ReplayKeepsWorkingTests(unittest.TestCase):
    """The 0.8.0 pending-start fix must survive the audit fixes."""

    def test_a_start_during_the_shutdown_is_still_replayed(self) -> None:
        states = {"light.a": FakeState("on")}
        scheduler, hass = make_scheduler(
            SCHEDULER, states, zone("light.a"), store=FakeStore()
        )
        now = datetime.now(UTC)
        scheduler._active = True
        scheduler._stopping = True
        scheduler._run_targets = ["light.a"]
        scheduler._started_at = now
        scheduler._finishes_at = now

        async def scenario():
            await scheduler._async_scheduled_start(
                now,
                {
                    "id": "s1",
                    "time": "18:30",
                    "days": [0],
                    "duration": 7200,
                    "interval": 0,
                    "target_entity_ids": [],
                    "enabled": True,
                },
            )
            scheduler._active = False
            scheduler._stopping = False
            await scheduler._async_replay_pending_start()

        asyncio.run(scenario())

        self.assertEqual([("turn_on", "light.a")], hass.calls)


if __name__ == "__main__":
    unittest.main()
