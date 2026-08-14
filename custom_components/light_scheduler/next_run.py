"""Pure, timezone-safe helpers for scheduling light groups."""
from __future__ import annotations
from datetime import datetime, time, timedelta, timezone
from typing import Any

def _parse_time(value: Any) -> time | None:
    if isinstance(value, time): return value
    if not isinstance(value, str): return None
    try:
        parts = [int(part) for part in value.split(":")]
        return time(*parts) if len(parts) in (2, 3) else None
    except (TypeError, ValueError): return None

def _exists(candidate: datetime) -> bool:
    if candidate.tzinfo is None: return True
    roundtrip = candidate.astimezone(timezone.utc).astimezone(candidate.tzinfo)
    return roundtrip.replace(fold=0) == candidate or roundtrip.replace(fold=1) == candidate

def find_next_run(schedules: list[dict[str, Any]], now: datetime, enabled: bool = True) -> tuple[datetime | None, dict[str, Any] | None]:
    """Return the next valid local-time schedule and its source record."""
    if not enabled: return None, None
    best: tuple[datetime, dict[str, Any]] | None = None
    current = now.astimezone(timezone.utc) if now.tzinfo else now
    for offset in range(8):
        date = (now + timedelta(days=offset)).date()
        for schedule in schedules:
            days = schedule.get("days", [])
            schedule_time = _parse_time(schedule.get("time"))
            if not schedule.get("enabled", True) or not isinstance(days, (list, tuple)) or date.weekday() not in days or schedule_time is None: continue
            candidate = datetime.combine(date, schedule_time, tzinfo=now.tzinfo)
            instant = candidate.astimezone(timezone.utc) if candidate.tzinfo else candidate
            if not _exists(candidate) or instant <= current: continue
            if best is None or instant < (best[0].astimezone(timezone.utc) if best[0].tzinfo else best[0]): best = (candidate, schedule)
    return best if best else (None, None)
