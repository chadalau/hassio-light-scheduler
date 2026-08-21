"""Pure, timezone-safe helpers for scheduling light groups."""
from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any

# How many days ahead an occurrence is searched for. Seven would already cover
# every weekday; the eighth absorbs a schedule that only exists later today.
SEARCH_DAYS = 8


def _parse_time(value: Any) -> time | None:
    if isinstance(value, time): return value
    if not isinstance(value, str): return None
    try:
        parts = [int(part) for part in value.split(":")]
        return time(*parts) if len(parts) in (2, 3) else None
    except (TypeError, ValueError): return None

def _exists(candidate: datetime) -> bool:
    if candidate.tzinfo is None: return True
    roundtrip = candidate.astimezone(UTC).astimezone(candidate.tzinfo)
    return roundtrip.replace(fold=0) == candidate or roundtrip.replace(fold=1) == candidate


def is_ambiguous(candidate: datetime) -> bool:
    """Return whether this local time happens twice (end of DST).

    ``datetime.combine`` produces ``fold=0``, so an hour that repeats would be
    resolved to its first occurrence with nothing telling the user that a second
    one exists. Detecting it lets the zone warn instead of silently picking one.
    """
    if candidate.tzinfo is None:
        return False
    return (
        candidate.replace(fold=0).utcoffset()
        != candidate.replace(fold=1).utcoffset()
    )


def _occurrences(
    schedule: dict[str, Any], now: datetime
) -> list[datetime]:
    """Return this schedule's upcoming local start times, earliest first."""
    days = schedule.get("days", [])
    schedule_time = _parse_time(schedule.get("time"))
    if (
        not schedule.get("enabled", True)
        or not isinstance(days, (list, tuple))
        or schedule_time is None
    ):
        return []
    current = now.astimezone(UTC) if now.tzinfo else now
    found: list[datetime] = []
    for offset in range(SEARCH_DAYS):
        date = (now + timedelta(days=offset)).date()
        if date.weekday() not in days:
            continue
        # Policy for a repeated local hour: always the FIRST occurrence
        # (fold=0). Picking the second one would keep the lights off for an
        # extra hour on exactly one night a year; is_ambiguous() surfaces the
        # choice so the user can move the schedule instead.
        candidate = datetime.combine(date, schedule_time, tzinfo=now.tzinfo).replace(
            fold=0
        )
        instant = candidate.astimezone(UTC) if candidate.tzinfo else candidate
        if not _exists(candidate) or instant <= current:
            continue
        found.append(candidate)
    return found


def _instant(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value


def find_next_run(schedules: list[dict[str, Any]], now: datetime, enabled: bool = True) -> tuple[datetime | None, dict[str, Any] | None]:
    """Return the next valid local-time schedule and its source record."""
    if not enabled: return None, None
    best: tuple[datetime, dict[str, Any]] | None = None
    for schedule in schedules:
        occurrences = _occurrences(schedule, now)
        if not occurrences:
            continue
        candidate = occurrences[0]
        if best is None or _instant(candidate) < _instant(best[0]):
            best = (candidate, schedule)
    return best if best else (None, None)


def ambiguous_schedule_ids(
    schedules: list[dict[str, Any]], now: datetime
) -> list[str]:
    """Return the ids whose next start falls on a repeated local hour."""
    return [
        str(schedule.get("id"))
        for schedule in schedules
        if schedule.get("id")
        and any(is_ambiguous(candidate) for candidate in _occurrences(schedule, now)[:1])
    ]
