from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from vietlott.cli import main
from vietlott.config import get_game
from vietlott.errors import FreshnessError
from vietlott.freshness import check_freshness
from vietlott.models import DrawRecord, NumberSetResult
from vietlott.storage import DataStore

TZ = datetime.fromisoformat("2026-08-03T00:00:00+07:00").tzinfo


def lotto(draw_id: str, draw_date: str, hour: int, slot: str) -> DrawRecord:
    return DrawRecord(
        game="lotto535",
        draw_id=draw_id,
        draw_date=draw_date,
        draw_time=f"{hour:02d}:00:00",
        draw_slot=slot,
        result=NumberSetResult(main_numbers=[1, 2, 3, 4, 5], bonus_numbers=[6]),
        source_url=get_game("lotto535").endpoint,
        source_sha256="a" * 64,
        retrieved_at="2026-08-03T13:10:00+07:00",
    )


def report_at(store: DataStore, value: str) -> dict[str, Any]:
    return check_freshness(
        store,
        ["lotto535"],
        as_of=datetime.fromisoformat(value),
    )


@pytest.mark.parametrize(
    ("as_of", "status"),
    [
        ("2026-08-03T13:59:59+07:00", "pending"),
        ("2026-08-03T14:00:00+07:00", "pending"),
        ("2026-08-03T14:00:01+07:00", "stale"),
    ],
)
def test_freshness_before_at_and_after_deadline(
    tmp_path: Path, as_of: str, status: str
) -> None:
    store = DataStore(tmp_path / "data")
    store.upsert("lotto535", [lotto("00800", "2026-08-02", 21, "night")])
    report = report_at(store, as_of)
    assert report["overall_status"] == status
    game = report["games"][0]
    assert game["expected"]["draw_id"] == "00801"


def test_freshness_handles_both_daily_lotto_draws(tmp_path: Path) -> None:
    store = DataStore(tmp_path / "data")
    store.upsert("lotto535", [lotto("00801", "2026-08-03", 13, "afternoon")])
    assert report_at(store, "2026-08-03T14:01:00+07:00")["overall_status"] == "fresh"
    assert report_at(store, "2026-08-03T21:30:00+07:00")["overall_status"] == "pending"
    stale = report_at(store, "2026-08-03T22:00:01+07:00")
    assert stale["overall_status"] == "stale"
    assert stale["games"][0]["expected"]["draw_id"] == "00802"


def test_freshness_can_check_published_api(tmp_path: Path) -> None:
    record = lotto("00801", "2026-08-03", 13, "afternoon")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/lotto535/latest.json"
        return httpx.Response(200, json=record.to_dict())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = check_freshness(
            DataStore(tmp_path / "unused"),
            ["lotto535"],
            as_of=datetime(2026, 8, 3, 14, 1, tzinfo=TZ),
            api_base_url="https://example.test/api/v1",
            client=client,
        )
    assert report["overall_status"] == "fresh"
    assert report["source"] == {"kind": "api", "value": "https://example.test/api/v1"}


def test_freshness_retries_a_slow_pages_deployment(tmp_path: Path) -> None:
    record = lotto("00801", "2026-08-03", 13, "afternoon")
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, request=request)
        return httpx.Response(200, json=record.to_dict(), request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        patch("vietlott.freshness.time_module.sleep") as sleep,
    ):
        report = check_freshness(
            DataStore(tmp_path / "unused"),
            ["lotto535"],
            as_of=datetime(2026, 8, 3, 14, 1, tzinfo=TZ),
            api_base_url="https://example.test/api/v1",
            client=client,
        )
    assert report["overall_status"] == "fresh"
    assert attempts == 3
    assert [call.args[0] for call in sleep.call_args_list] == [1, 2]


def test_missing_lotto_slot_is_invalid(tmp_path: Path) -> None:
    store = DataStore(tmp_path / "data")
    record = lotto("00801", "2026-08-03", 13, "afternoon")
    record.draw_time = None
    record.draw_slot = None
    store.upsert("lotto535", [record])
    assert report_at(store, "2026-08-03T14:01:00+07:00")["overall_status"] == "invalid"


def test_freshness_rejects_naive_as_of(tmp_path: Path) -> None:
    with pytest.raises(FreshnessError):
        check_freshness(
            DataStore(tmp_path / "data"),
            ["lotto535"],
            as_of=datetime(2026, 8, 3, 14),
        )


def test_freshness_rejects_insecure_remote_api_url(tmp_path: Path) -> None:
    with pytest.raises(FreshnessError):
        check_freshness(
            DataStore(tmp_path / "data"),
            ["lotto535"],
            as_of=datetime(2026, 8, 3, 14, tzinfo=TZ),
            api_base_url="http://example.test/api/v1",
        )


def test_freshness_normalizes_utc_to_vietnam_time(tmp_path: Path) -> None:
    store = DataStore(tmp_path / "data")
    store.upsert("lotto535", [lotto("00800", "2026-08-02", 21, "night")])
    report = report_at(store, "2026-08-03T07:00:00+00:00")
    assert report["as_of"] == "2026-08-03T14:00:00+07:00"
    assert report["overall_status"] == "pending"


def test_cli_fails_only_after_sla_deadline(tmp_path: Path) -> None:
    store = DataStore(tmp_path / "data")
    store.upsert("lotto535", [lotto("00800", "2026-08-02", 21, "night")])
    common = ["--data-dir", str(store.root), "check-freshness", "--games", "lotto535"]
    assert main([*common, "--as-of", "2026-08-03T14:00:00+07:00"]) == 0
    assert main([*common, "--as-of", "2026-08-03T14:00:01+07:00"]) == 1
