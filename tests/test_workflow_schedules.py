from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRON_RE = re.compile(r'- cron: "([0-9,]+) ([0-9,]+) \* \* \*"')


def _scheduled_local_minutes(workflow: str) -> set[int]:
    scheduled: set[int] = set()
    for minute_field, hour_field in CRON_RE.findall(workflow):
        for minute in map(int, minute_field.split(",")):
            for hour in map(int, hour_field.split(",")):
                scheduled.add(((hour + 7) % 24) * 60 + minute)
    return scheduled


def test_pages_uses_eight_utc_cron_runs_staggered_after_ops() -> None:
    workflow = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")

    assert 'cron: "15,30,45 6,11,14 * * *"' in workflow
    assert 'cron: "0,15,30,45 7,12,15 * * *"' in workflow
    assert 'cron: "0 8,13,16 * * *"' in workflow
    assert "timezone:" not in workflow

    local_minutes = _scheduled_local_minutes(workflow)
    expected_offsets = [15, 30, 45, 60, 75, 90, 105, 120]
    for draw_hour in (13, 18, 21):
        draw_minute = draw_hour * 60
        assert sorted(
            minute - draw_minute
            for minute in local_minutes
            if draw_minute < minute <= draw_minute + 120
        ) == expected_offsets


def test_pages_is_scheduled_or_manual_but_not_triggered_by_ci() -> None:
    workflow = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")

    assert "schedule:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "workflow_run:" not in workflow
    assert "github.event.workflow_run" not in workflow
    assert "contents: write" not in workflow
    assert "self-hosted" not in workflow
    assert "vietlott collect" not in workflow
