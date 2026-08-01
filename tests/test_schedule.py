from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from vietlott.schedule import due_games

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def test_due_games_at_wednesday_18h22() -> None:
    due = due_games(datetime(2026, 7, 29, 18, 22, tzinfo=TZ))
    assert set(due) == {"mega645", "max3d"}


def test_due_games_at_daily_lotto_draw() -> None:
    assert due_games(datetime(2026, 8, 1, 13, 37, tzinfo=TZ)) == ["lotto535"]
    assert due_games(datetime(2026, 8, 1, 21, 22, tzinfo=TZ)) == ["lotto535"]


def test_delay_grace_window() -> None:
    due = due_games(datetime(2026, 8, 1, 19, 5, tzinfo=TZ))
    assert set(due) == {"power655", "max3d_pro"}
    assert due_games(datetime(2026, 8, 1, 19, 31, tzinfo=TZ)) == []
