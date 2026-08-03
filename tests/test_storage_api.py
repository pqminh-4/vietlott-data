from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from vietlott.adapters import get_adapter
from vietlott.api import build_api
from vietlott.config import get_game
from vietlott.errors import ValidationError
from vietlott.models import DrawRecord, NumberSetResult, Prize
from vietlott.storage import BackfillState, DataStore


def record(
    draw_id: str = "00001", *, retrieved_at: str = "2026-08-01T00:00:00+00:00"
) -> DrawRecord:
    return DrawRecord(
        game="mega645",
        draw_id=draw_id,
        draw_date="2026-07-31",
        draw_time="18:00:00",
        draw_slot="evening",
        result=NumberSetResult(main_numbers=[1, 2, 3, 4, 5, 6]),
        prizes=[Prize(code="jackpot", name="Jackpot", jackpot_vnd=12_000_000_000)],
        source_url="https://vietlott.vn/ajaxpro/result.ashx",
        source_sha256="a" * 64,
        retrieved_at=retrieved_at,
    )


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_upsert_is_semantic_and_deterministic(tmp_path: Path) -> None:
    store = DataStore(tmp_path / "data")
    assert store.upsert("mega645", [record()]) == 1
    refreshed = replace(record(retrieved_at="2026-08-02T00:00:00+00:00"), source_sha256="b" * 64)
    assert store.upsert("mega645", [refreshed]) == 0
    assert store.load("mega645")[0].retrieved_at == "2026-08-01T00:00:00+00:00"


def test_upsert_preserves_enrichment_and_accepts_later_correction(tmp_path: Path) -> None:
    store = DataStore(tmp_path / "data")
    enriched = replace(
        record(),
        source_pdf_url="https://media.vietlott.vn/result.pdf",
        source_pdf_sha256="c" * 64,
    )
    assert store.upsert("mega645", [enriched]) == 1
    thinner = replace(
        record(retrieved_at="2026-08-02T00:00:00+00:00"),
        draw_time=None,
        draw_slot=None,
        prizes=[],
    )
    assert store.upsert("mega645", [thinner]) == 0
    preserved = store.load("mega645")[0]
    assert preserved.draw_time == "18:00:00"
    assert preserved.draw_slot == "evening"
    assert preserved.prizes == enriched.prizes
    assert preserved.source_pdf_url == enriched.source_pdf_url
    assert preserved.source_pdf_sha256 == enriched.source_pdf_sha256

    unresolved = replace(record("00002"), draw_time=None, draw_slot=None)
    assert store.upsert("mega645", [unresolved]) == 1
    corrected = replace(unresolved, draw_time="18:00:00", draw_slot="evening")
    assert store.upsert("mega645", [corrected]) == 1
    assert store.load("mega645")[1].draw_time == "18:00:00"


def test_upsert_persists_a_new_official_pdf_hash(tmp_path: Path) -> None:
    store = DataStore(tmp_path / "data")
    pdf_url = "https://media.vietlott.vn/result.pdf"
    without_hash = replace(record(), source_pdf_url=pdf_url)
    assert store.upsert("mega645", [without_hash]) == 1
    audited = replace(without_hash, source_pdf_sha256="d" * 64)
    assert store.upsert("mega645", [audited]) == 1
    assert store.load("mega645")[0].source_pdf_sha256 == "d" * 64


def test_upsert_ignores_regenerated_pdf_for_unchanged_draw(tmp_path: Path) -> None:
    store = DataStore(tmp_path / "data")
    original = replace(
        record(),
        source_pdf_url="https://media.vietlott.vn/result.pdf",
        source_pdf_sha256="c" * 64,
    )
    assert store.upsert("mega645", [original]) == 1
    regenerated = replace(
        original,
        retrieved_at="2026-08-02T00:00:00+00:00",
        source_sha256="b" * 64,
        source_pdf_sha256="d" * 64,
    )

    assert store.upsert("mega645", [regenerated]) == 0
    assert store.load("mega645")[0] == original


def test_api_and_csv_are_reproducible(tmp_path: Path) -> None:
    store = DataStore(tmp_path / "data")
    store.upsert("mega645", [record()])
    site = tmp_path / "site"
    build_api(store, site)
    first_hash = tree_hash(site)
    build_api(store, site)
    assert tree_hash(site) == first_hash
    index = json.loads((site / "api" / "v1" / "index.json").read_text(encoding="utf-8"))
    assert index["schema_version"] == "1.0"
    assert (site / "api" / "v1" / "mega645" / "draws" / "00001.json").exists()
    assert (site / "api" / "v1" / "downloads" / "mega645-prizes.csv").exists()


def test_complete_coverage_requires_explained_gaps(tmp_path: Path) -> None:
    store = DataStore(tmp_path / "data")
    store.upsert("mega645", [record("00001"), record("00003")])
    store.save_state("mega645", BackfillState(next_page_index=10, complete=True))
    coverage = store.coverage("mega645")
    assert coverage["missing_ids"] == ["00002"]
    with pytest.raises(ValidationError):
        store.validate_game("mega645")


def test_validation_rejects_stale_tracked_coverage(tmp_path: Path) -> None:
    store = DataStore(tmp_path / "data")
    store.upsert("mega645", [record()])
    coverage_path = store.root / "coverage" / "mega645.json"
    payload = json.loads(coverage_path.read_text(encoding="utf-8"))
    payload["record_count"] = 0
    coverage_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValidationError, match="coverage is stale"):
        store.validate_game("mega645")


def test_fixture_to_jsonl_csv_api_and_validation(tmp_path: Path, official_response) -> None:
    parsed = get_adapter("mega645").parse_page(
        official_response("mega645", get_game("mega645").endpoint)
    )
    store = DataStore(tmp_path / "data")
    assert store.upsert("mega645", parsed) == 1
    assert store.validate_all()["mega645"] == 1
    site = tmp_path / "site"
    build_api(store, site)
    draw_id = parsed[0].draw_id
    assert (site / "api" / "v1" / "mega645" / "draws" / f"{draw_id}.json").exists()
    assert (site / "api" / "v1" / "downloads" / "mega645-results.csv").exists()
