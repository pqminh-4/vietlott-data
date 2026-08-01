"""Atomic JSONL storage, checkpoints, coverage, and deterministic CSV exports."""

from __future__ import annotations

import csv
import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vietlott.config import GAMES
from vietlott.errors import ValidationError
from vietlott.models import DrawRecord, NumberSetResult, ThreeDigitResult


@dataclass(slots=True)
class BackfillState:
    next_page_index: int = 0
    complete: bool = False
    unavailable_ranges: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "next_page_index": self.next_page_index,
            "unavailable_ranges": self.unavailable_ranges,
        }


class DataStore:
    def __init__(self, root: Path | str = "data") -> None:
        self.root = Path(root)

    def canonical_path(self, game: str) -> Path:
        return self.root / "canonical" / f"{game}.jsonl"

    def load(self, game: str) -> list[DrawRecord]:
        path = self.canonical_path(game)
        if not path.exists():
            return []
        records: list[DrawRecord] = []
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                    records.append(DrawRecord.from_dict(payload))
                except (json.JSONDecodeError, KeyError, TypeError, ValidationError) as exc:
                    raise ValidationError(f"Invalid {path}:{line_number}: {exc}") from exc
        return records

    def upsert(self, game: str, incoming: Iterable[DrawRecord]) -> int:
        existing = {record.draw_id: record for record in self.load(game)}
        changed = 0
        for record in incoming:
            record.validate()
            if record.game != game:
                raise ValidationError(f"Cannot store {record.game} in the {game} dataset")
            previous = existing.get(record.draw_id)
            if previous is None or _semantic_payload(previous) != _semantic_payload(record):
                existing[record.draw_id] = record
                changed += 1
        if changed:
            self._write_jsonl(self.canonical_path(game), sorted(existing.values(), key=_sort_key))
        self.write_coverage(game)
        return changed

    def validate_game(self, game: str) -> int:
        records = self.load(game)
        keys = [record.key for record in records]
        if len(keys) != len(set(keys)):
            raise ValidationError(f"Duplicate draw key in {game}")
        if records != sorted(records, key=_sort_key):
            raise ValidationError(f"Canonical JSONL is not sorted for {game}")
        coverage = self.coverage(game)
        if coverage["backfill_complete"]:
            unexplained = _unexplained_missing(
                coverage["missing_ids"], coverage["unavailable_ranges"]
            )
            if unexplained:
                raise ValidationError(
                    f"{game} backfill is marked complete but has unexplained missing draws: "
                    f"{unexplained[:10]}"
                )
        return len(records)

    def validate_all(self, games: Iterable[str] | None = None) -> dict[str, int]:
        return {game: self.validate_game(game) for game in (games or GAMES)}

    def load_state(self, game: str) -> BackfillState:
        path = self.root / "state" / f"{game}.json"
        if not path.exists():
            return BackfillState()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return BackfillState(
            next_page_index=int(payload.get("next_page_index", 0)),
            complete=bool(payload.get("complete", False)),
            unavailable_ranges=list(payload.get("unavailable_ranges", [])),
        )

    def save_state(self, game: str, state: BackfillState) -> bool:
        changed = _write_json_if_changed(self.root / "state" / f"{game}.json", state.to_dict())
        self.write_coverage(game)
        return changed

    def coverage(self, game: str) -> dict[str, Any]:
        records = self.load(game)
        state = self.load_state(game)
        ids = sorted({int(record.draw_id) for record in records})
        id_set = set(ids)
        width = max((len(record.draw_id) for record in records), default=5)
        missing = (
            [str(value).zfill(width) for value in range(ids[0], ids[-1] + 1) if value not in id_set]
            if ids
            else []
        )
        return {
            "game": game,
            "record_count": len(records),
            "earliest_draw_id": min(records, key=_sort_key).draw_id if records else None,
            "earliest_draw_date": min((record.draw_date for record in records), default=None),
            "latest_draw_id": max(records, key=_sort_key).draw_id if records else None,
            "latest_draw_date": max((record.draw_date for record in records), default=None),
            "missing_ids": missing,
            "unavailable_ranges": state.unavailable_ranges,
            "backfill_complete": state.complete,
            "next_page_index": state.next_page_index,
        }

    def write_coverage(self, game: str) -> bool:
        return _write_json_if_changed(self.root / "coverage" / f"{game}.json", self.coverage(game))

    def write_csv(self, game: str) -> dict[str, Path]:
        records = self.load(game)
        output_dir = self.root / "csv" / game
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = {
            "draws": output_dir / "draws.csv",
            "results": output_dir / "results.csv",
            "prizes": output_dir / "prizes.csv",
        }
        _write_csv_atomic(
            paths["draws"],
            [
                "game",
                "draw_id",
                "draw_date",
                "draw_time",
                "draw_slot",
                "result_kind",
                "main_numbers",
                "bonus_numbers",
                "source_url",
                "source_sha256",
                "retrieved_at",
            ],
            (_draw_csv_row(record) for record in records),
        )
        _write_csv_atomic(
            paths["results"],
            ["game", "draw_id", "tier_code", "tier_name", "ordinal", "number"],
            (row for record in records for row in _result_csv_rows(record)),
        )
        _write_csv_atomic(
            paths["prizes"],
            [
                "game",
                "draw_id",
                "prize_code",
                "prize_name",
                "amount_vnd",
                "winner_count",
                "jackpot_vnd",
            ],
            (row for record in records for row in _prize_csv_rows(record)),
        )
        return paths

    @staticmethod
    def _write_jsonl(path: Path, records: Iterable[DrawRecord]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", delete=False, dir=path.parent
        ) as stream:
            temporary = Path(stream.name)
            for record in records:
                stream.write(_compact_json(record.to_dict()) + "\n")
        os.replace(temporary, path)


def _sort_key(record: DrawRecord) -> tuple[str, str, int]:
    return record.draw_date, record.draw_time or "", int(record.draw_id)


def _semantic_payload(record: DrawRecord) -> dict[str, Any]:
    payload = record.to_dict()
    for field_name in ("retrieved_at", "source_sha256", "source_pdf_sha256"):
        payload.pop(field_name, None)
    return payload


def _compact_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _write_json_if_changed(path: Path, payload: dict[str, Any]) -> bool:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == rendered:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", delete=False, dir=path.parent
    ) as stream:
        temporary = Path(stream.name)
        stream.write(rendered)
    os.replace(temporary, path)
    return True


def _write_csv_atomic(path: Path, headers: list[str], rows: Iterable[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", delete=False, dir=path.parent
    ) as stream:
        temporary = Path(stream.name)
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(headers)
        writer.writerows(rows)
    os.replace(temporary, path)


def _draw_csv_row(record: DrawRecord) -> list[Any]:
    if isinstance(record.result, NumberSetResult):
        main = " ".join(str(value) for value in record.result.main_numbers)
        bonus = " ".join(str(value) for value in record.result.bonus_numbers)
    else:
        main = bonus = ""
    return [
        record.game,
        record.draw_id,
        record.draw_date,
        record.draw_time or "",
        record.draw_slot or "",
        record.result.kind,
        main,
        bonus,
        record.source_url,
        record.source_sha256,
        record.retrieved_at,
    ]


def _result_csv_rows(record: DrawRecord) -> list[list[Any]]:
    rows: list[list[Any]] = []
    if isinstance(record.result, NumberSetResult):
        for tier_code, name, values in (
            ("main", "Main numbers", record.result.main_numbers),
            ("bonus", "Bonus numbers", record.result.bonus_numbers),
        ):
            rows.extend(
                [record.game, record.draw_id, tier_code, name, index, value]
                for index, value in enumerate(values, start=1)
            )
    elif isinstance(record.result, ThreeDigitResult):
        for tier in record.result.tiers:
            rows.extend(
                [record.game, record.draw_id, tier.code, tier.name, index, value]
                for index, value in enumerate(tier.numbers, start=1)
            )
    return rows


def _prize_csv_rows(record: DrawRecord) -> list[list[Any]]:
    return [
        [
            record.game,
            record.draw_id,
            prize.code,
            prize.name,
            "" if prize.amount_vnd is None else prize.amount_vnd,
            "" if prize.winner_count is None else prize.winner_count,
            "" if prize.jackpot_vnd is None else prize.jackpot_vnd,
        ]
        for prize in record.prizes
    ]


def _unexplained_missing(
    missing_ids: list[str], unavailable_ranges: list[dict[str, str]]
) -> list[str]:
    def explained(value: str) -> bool:
        numeric = int(value)
        return any(
            int(item["start"]) <= numeric <= int(item["end"]) and bool(item.get("reason"))
            for item in unavailable_ranges
        )

    return [value for value in missing_ids if not explained(value)]
