"""Asia/Ho_Chi_Minh draw schedule selection."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from vietlott.config import GAMES

TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")


def local_now() -> datetime:
    return datetime.now(TIMEZONE)


def due_games(now: datetime | None = None, *, grace_minutes: int = 90) -> list[str]:
    """Return products whose draw time is recent enough for a scheduled poll."""
    current = (now or local_now()).astimezone(TIMEZONE)
    due: list[str] = []
    for code, spec in GAMES.items():
        if current.weekday() not in spec.weekdays:
            continue
        for hour in spec.draw_hours:
            draw_time = current.replace(hour=hour, minute=0, second=0, microsecond=0)
            if timedelta(0) <= current - draw_time <= timedelta(minutes=grace_minutes):
                due.append(code)
                break
    return due
