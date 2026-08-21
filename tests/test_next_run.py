"""Regression tests for timezone-safe schedule selection."""

from __future__ import annotations

import importlib.util
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "light_scheduler"
    / "next_run.py"
)
SPEC = importlib.util.spec_from_file_location("light_scheduler_next_run", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
find_next_run = MODULE.find_next_run


class FindNextRunTests(unittest.TestCase):
    """Cover recurring schedules, disabled rows and DST gaps."""

    def test_returns_next_enabled_schedule(self) -> None:
        local_timezone = timezone(timedelta(hours=-3))
        now = datetime(2026, 8, 14, 10, 0, tzinfo=local_timezone)
        disabled = {"time": "10:30", "days": [4], "enabled": False}
        expected = {"time": "11:15", "days": [4], "enabled": True}

        instant, schedule = find_next_run([disabled, expected], now)

        self.assertEqual(datetime(2026, 8, 14, 11, 15, tzinfo=local_timezone), instant)
        self.assertIs(expected, schedule)

    def test_rolls_an_elapsed_schedule_to_next_week(self) -> None:
        local_timezone = timezone(timedelta(hours=-3))
        now = datetime(2026, 8, 14, 20, 0, tzinfo=local_timezone)
        schedule = {"time": "18:30", "days": [4], "enabled": True}

        instant, _ = find_next_run([schedule], now)

        self.assertEqual(datetime(2026, 8, 21, 18, 30, tzinfo=local_timezone), instant)

    def test_ignores_nonexistent_dst_time(self) -> None:
        try:
            local_timezone = ZoneInfo("America/New_York")
        except ZoneInfoNotFoundError:
            self.skipTest("Timezone database is not installed")
        now = datetime(2026, 3, 7, 23, 0, tzinfo=local_timezone)
        nonexistent = {"time": "02:30", "days": [6], "enabled": True}
        valid = {"time": "03:30", "days": [6], "enabled": True}

        instant, schedule = find_next_run([nonexistent, valid], now)

        self.assertEqual(datetime(2026, 3, 8, 3, 30, tzinfo=local_timezone), instant)
        self.assertIs(valid, schedule)

    def test_disabled_zone_has_no_next_run(self) -> None:
        now = datetime(2026, 8, 14, 10, 0, tzinfo=UTC)
        schedule = {"time": "11:00", "days": [4], "enabled": True}

        self.assertEqual((None, None), find_next_run([schedule], now, False))


if __name__ == "__main__":
    unittest.main()
