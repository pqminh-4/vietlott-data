"""Build the versioned static GitHub Pages API."""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from vietlott.config import GAMES
from vietlott.storage import DataStore


def build_api(store: DataStore, site_root: Path | str = "site") -> dict[str, Any]:
    site = Path(site_root)
    api_root = site / "api" / "v1"
    if api_root.exists():
        shutil.rmtree(api_root)
    api_root.mkdir(parents=True, exist_ok=True)
    downloads = api_root / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)

    generated_values: list[str] = []
    games_index: list[dict[str, Any]] = []
    for game, spec in GAMES.items():
        records = store.load(game)
        game_root = api_root / game
        draws_root = game_root / "draws"
        years_root = game_root / "years"
        draws_root.mkdir(parents=True, exist_ok=True)
        years_root.mkdir(parents=True, exist_ok=True)
        by_year: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            payload = record.to_dict()
            _write_json(draws_root / f"{record.draw_id}.json", payload)
            by_year[record.draw_date[:4]].append(payload)
            generated_values.append(record.retrieved_at)
        for year, values in sorted(by_year.items()):
            _write_json(years_root / f"{year}.json", values)
        latest = max(
            records,
            key=lambda item: (item.draw_date, item.draw_time or "", int(item.draw_id)),
            default=None,
        )
        _write_json(game_root / "latest.json", latest.to_dict() if latest else None)
        coverage = store.coverage(game)
        _write_json(game_root / "coverage.json", coverage)
        csv_paths = store.write_csv(game)
        for table, source in csv_paths.items():
            shutil.copyfile(source, downloads / f"{game}-{table}.csv")
        games_index.append(
            {
                "code": game,
                "display_name": spec.display_name,
                "kind": spec.kind,
                "latest_draw_id": latest.draw_id if latest else None,
                "record_count": len(records),
                "paths": {
                    "coverage": f"{game}/coverage.json",
                    "latest": f"{game}/latest.json",
                    "years": f"{game}/years/",
                },
            }
        )
    index = {
        "api_version": "v1",
        "schema_version": "1.0",
        "generated_at": max(generated_values, default=None),
        "games": games_index,
    }
    _write_json(api_root / "index.json", index)
    site.mkdir(parents=True, exist_ok=True)
    (site / ".nojekyll").write_text("", encoding="utf-8")
    return index


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
