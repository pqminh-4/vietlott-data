from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from vietlott.config import get_game
from vietlott.errors import ParseError
from vietlott.http import OfficialResponse
from vietlott.models import DrawRecord, NumberSetResult
from vietlott.service import CollectionSummary, Collector
from vietlott.storage import DataStore


def make_record(draw_id: str, draw_date: str) -> DrawRecord:
    return DrawRecord(
        game="mega645",
        draw_id=draw_id,
        draw_date=draw_date,
        result=NumberSetResult(main_numbers=[1, 2, 3, 4, 5, 6]),
        source_url=get_game("mega645").endpoint,
        source_sha256="a" * 64,
        retrieved_at="2026-08-01T00:00:00+00:00",
    )


def make_lotto_record(
    draw_id: str,
    draw_date: str,
    draw_time: str,
    draw_slot: str,
) -> DrawRecord:
    return DrawRecord(
        game="lotto535",
        draw_id=draw_id,
        draw_date=draw_date,
        draw_time=draw_time,
        draw_slot=draw_slot,
        result=NumberSetResult(main_numbers=[1, 2, 3, 4, 5], bonus_numbers=[6]),
        source_url=get_game("lotto535").endpoint,
        source_sha256="a" * 64,
        retrieved_at="2026-08-04T08:00:00+00:00",
    )


class FakeAdapter:
    spec = get_game("mega645")

    def fetch_page(self, _client, index: int):
        response = OfficialResponse(
            url=self.spec.endpoint,
            content=str(index).encode(),
            retrieved_at="2026-08-01T00:00:00+00:00",
            html="",
        )
        values = {
            0: [make_record("00002", "2026-07-31")],
            1: [make_record("00001", "2026-07-29")],
            2: [],
            3: [],
        }
        return response, values.get(index, [])

    def fetch_detail(self, _client, record: DrawRecord) -> DrawRecord:
        return replace(record)


class FakeClient:
    pass


class TruncatedHistoryAdapter(FakeAdapter):
    def fetch_page(self, _client, index: int):
        response = OfficialResponse(
            url=self.spec.endpoint,
            content=str(index).encode(),
            retrieved_at="2026-08-01T00:00:00+00:00",
            html="",
        )
        values = {
            0: [make_record("00011", "2026-07-31")],
            1: [make_record("00010", "2026-07-29")],
            2: [],
            3: [],
        }
        return response, values.get(index, [])


class InvalidDetailAdapter(FakeAdapter):
    def fetch_detail(self, _client, record: DrawRecord) -> DrawRecord:
        raise ParseError(f"invalid official detail for {record.key}")


def test_backfill_resumes_and_completes(tmp_path: Path) -> None:
    store = DataStore(tmp_path / "data")
    collector = Collector(store, FakeClient(), max_workers=2)  # type: ignore[arg-type]
    with patch("vietlott.service.get_adapter", return_value=FakeAdapter()):
        first = collector.backfill(["mega645"], resume=True, max_draws=32)
        second = collector.backfill(["mega645"], resume=True, max_draws=32)
    assert first.changed == {"mega645": 2}
    assert first.completed_backfills == ["mega645"]
    assert second.fetched == {"mega645": 0}
    assert store.load_state("mega645").next_page_index == 4
    assert [item.draw_id for item in store.load("mega645")] == ["00001", "00002"]


def test_dry_run_does_not_write(tmp_path: Path) -> None:
    store = DataStore(tmp_path / "data")
    collector = Collector(store, FakeClient(), max_workers=1)  # type: ignore[arg-type]
    with patch("vietlott.service.get_adapter", return_value=FakeAdapter()):
        summary = collector.collect_latest(["mega645"], dry_run=True)
    assert summary.fetched == {"mega645": 1}
    assert store.load("mega645") == []
    assert summary.telemetry["mega645"]["before_latest_draw_id"] is None
    assert summary.telemetry["mega645"]["fetched_latest_draw_id"] == "00002"
    assert summary.telemetry["mega645"]["stored_latest_draw_id"] is None
    assert summary.telemetry["mega645"]["status"] == "dry-run"
    assert summary.to_dict()["selected_games"] == ["mega645"]


@pytest.mark.parametrize("minute", [40, 55])
def test_scheduled_collection_recovers_draw_at_late_retry(
    tmp_path: Path, minute: int
) -> None:
    store = DataStore(tmp_path / "data")
    store.upsert(
        "lotto535",
        [make_lotto_record("00802", "2026-08-03", "21:00:00", "night")],
    )
    collector = Collector(store, FakeClient(), max_workers=1)  # type: ignore[arg-type]
    # 14:40 and 14:55 are 100 and 115 minutes after the 13:00 draw,
    # beyond the normal 90-minute due-game window.
    now = datetime(2026, 8, 4, 14, minute, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    expected = CollectionSummary.empty()

    with (
        patch("vietlott.service.local_now", return_value=now),
        patch.object(collector, "collect_latest", return_value=expected) as collect_latest,
    ):
        summary = collector.run_scheduled()

    assert summary is expected
    collect_latest.assert_called_once_with(["lotto535"], audit_official_pdf=True)


@pytest.mark.parametrize("minute", [40, 55])
def test_scheduled_collection_does_not_refetch_current_draw_at_late_retry(
    tmp_path: Path, minute: int
) -> None:
    store = DataStore(tmp_path / "data")
    store.upsert(
        "lotto535",
        [make_lotto_record("00803", "2026-08-04", "13:00:00", "afternoon")],
    )
    collector = Collector(store, FakeClient(), max_workers=1)  # type: ignore[arg-type]
    now = datetime(2026, 8, 4, 14, minute, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))

    with (
        patch("vietlott.service.local_now", return_value=now),
        patch.object(collector, "collect_latest") as collect_latest,
    ):
        summary = collector.run_scheduled()

    assert summary.to_dict()["selected_games"] == []
    collect_latest.assert_not_called()


def test_backfill_reports_history_that_the_source_no_longer_returns(tmp_path: Path) -> None:
    store = DataStore(tmp_path / "data")
    collector = Collector(store, FakeClient(), max_workers=2)  # type: ignore[arg-type]
    with patch("vietlott.service.get_adapter", return_value=TruncatedHistoryAdapter()):
        collector.backfill(["mega645"], resume=True, max_draws=32)
    assert store.coverage("mega645")["unavailable_ranges"] == [
        {
            "start": "00001",
            "end": "00009",
            "reason": "official-source-no-longer-returns-older-draws",
        }
    ]


def test_invalid_source_does_not_modify_published_data(tmp_path: Path) -> None:
    store = DataStore(tmp_path / "data")
    store.upsert("mega645", [make_record("00001", "2026-07-29")])
    collector = Collector(store, FakeClient(), max_workers=1)  # type: ignore[arg-type]
    with (
        patch("vietlott.service.get_adapter", return_value=InvalidDetailAdapter()),
        pytest.raises(ParseError),
    ):
        collector.collect_latest(["mega645"])
    assert [item.draw_id for item in store.load("mega645")] == ["00001"]
