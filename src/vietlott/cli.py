"""Command-line interface for local use and GitHub Actions."""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from pathlib import Path

from vietlott.api import build_api
from vietlott.config import GAMES
from vietlott.errors import VietlottError
from vietlott.http import VietlottClient
from vietlott.schedule import due_games
from vietlott.service import Collector
from vietlott.storage import DataStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vietlott", description=__doc__)
    parser.add_argument("--data-dir", default="data", type=Path)
    parser.add_argument("--site-dir", default="site", type=Path)
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"])
    commands = parser.add_subparsers(dest="command", required=True)

    collect = commands.add_parser("collect", help="Collect the latest official result pages")
    collect.add_argument("--games", default="all")
    collect.add_argument(
        "--latest", action="store_true", help="Accepted for an explicit v1 contract"
    )
    collect.add_argument("--dry-run", action="store_true")
    collect.add_argument("--audit-pdf", action="store_true")

    backfill = commands.add_parser("backfill", help="Continue historical collection")
    backfill.add_argument("--game", default="all")
    backfill.add_argument("--resume", action="store_true")
    backfill.add_argument("--max-draws", type=int, default=250)
    backfill.add_argument("--audit-pdf", action="store_true")

    reconcile = commands.add_parser("reconcile", help="Repair recent gaps and continue backfill")
    reconcile.add_argument("--games", default="all")
    reconcile.add_argument("--recent-days", type=int, default=14)
    reconcile.add_argument("--max-backfill-draws", type=int, default=250)

    commands.add_parser("validate", help="Validate all canonical data and coverage")
    commands.add_parser("build-api", help="Build deterministic CSV and static API files")
    commands.add_parser("scheduled", help="Select collect or reconcile for the current local time")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    store = DataStore(args.data_dir)
    try:
        if args.command == "validate":
            print(json.dumps(store.validate_all(), ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "build-api":
            index = build_api(store, args.site_dir)
            print(json.dumps(index, ensure_ascii=False, sort_keys=True))
            return 0
        with VietlottClient() as client:
            collector = Collector(store, client)
            if args.command == "collect":
                games = _resolve_games(args.games)
                summary = collector.collect_latest(
                    games, dry_run=args.dry_run, audit_official_pdf=args.audit_pdf
                )
            elif args.command == "backfill":
                if args.max_draws < 1:
                    raise ValueError("--max-draws must be positive")
                summary = collector.backfill(
                    _resolve_games(args.game),
                    resume=args.resume,
                    max_draws=args.max_draws,
                    audit_official_pdf=args.audit_pdf,
                )
            elif args.command == "reconcile":
                summary = collector.reconcile(
                    _resolve_games(args.games),
                    recent_days=args.recent_days,
                    max_backfill_draws=args.max_backfill_draws,
                )
            elif args.command == "scheduled":
                summary = collector.run_scheduled()
            else:  # pragma: no cover - argparse makes this unreachable
                raise ValueError(f"Unknown command: {args.command}")
        print(json.dumps(summary.to_dict(), ensure_ascii=False, sort_keys=True))
        return 0
    except (VietlottError, ValueError) as exc:
        logging.getLogger(__name__).error("%s", exc)
        return 1


def _resolve_games(value: str) -> list[str]:
    if value == "all":
        return list(GAMES)
    if value == "due":
        return due_games()
    games = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [game for game in games if game not in GAMES]
    if unknown:
        raise ValueError(f"Unknown games: {', '.join(unknown)}")
    if not games:
        raise ValueError("At least one game is required")
    return games
