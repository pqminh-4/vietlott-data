"""Collection, enrichment, backfill, and reconciliation orchestration."""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from vietlott.adapters import BaseAdapter, get_adapter
from vietlott.config import GAMES, get_game
from vietlott.errors import ParseError
from vietlott.http import OfficialResponse, VietlottClient
from vietlott.models import DrawRecord
from vietlott.pdf import audit_pdf
from vietlott.schedule import due_games, local_now
from vietlott.storage import BackfillState, DataStore

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FetchedPage:
    index: int
    response: OfficialResponse
    records: list[DrawRecord]


@dataclass(slots=True)
class CollectionSummary:
    fetched: dict[str, int]
    changed: dict[str, int]
    completed_backfills: list[str]

    @classmethod
    def empty(cls) -> CollectionSummary:
        return cls(fetched={}, changed={}, completed_backfills=[])

    def merge(self, other: CollectionSummary) -> None:
        for game, count in other.fetched.items():
            self.fetched[game] = self.fetched.get(game, 0) + count
        for game, count in other.changed.items():
            self.changed[game] = self.changed.get(game, 0) + count
        self.completed_backfills.extend(
            game for game in other.completed_backfills if game not in self.completed_backfills
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "fetched": self.fetched,
            "changed": self.changed,
            "completed_backfills": self.completed_backfills,
        }


class Collector:
    def __init__(
        self,
        store: DataStore,
        client: VietlottClient,
        *,
        max_workers: int = 4,
    ) -> None:
        self.store = store
        self.client = client
        self.max_workers = max_workers

    def collect_latest(
        self,
        games: Iterable[str],
        *,
        dry_run: bool = False,
        audit_official_pdf: bool = False,
    ) -> CollectionSummary:
        summary = CollectionSummary.empty()
        for game in games:
            fetched = self._fetch_pages(get_adapter(game), [0], audit_official_pdf)
            if not fetched[0].records:
                raise ParseError(f"Latest official page for {game} contained no valid draws")
            records = _deduplicate(record for page in fetched for record in page.records)
            changed = 0 if dry_run else self.store.upsert(game, records)
            summary.fetched[game] = len(records)
            summary.changed[game] = changed
        return summary

    def backfill(
        self,
        games: Iterable[str],
        *,
        resume: bool = True,
        max_draws: int = 250,
        audit_official_pdf: bool = False,
    ) -> CollectionSummary:
        summary = CollectionSummary.empty()
        for game in games:
            spec = get_game(game)
            state = self.store.load_state(game)
            if state.complete and resume:
                summary.fetched[game] = 0
                summary.changed[game] = 0
                summary.completed_backfills.append(game)
                continue
            start_page = state.next_page_index if resume else 0
            page_count = max(1, max_draws // spec.page_size)
            indexes = list(range(start_page, start_page + page_count))
            fetched = self._fetch_pages(get_adapter(game), indexes, audit_official_pdf)
            records = _deduplicate(record for page in fetched for record in page.records)
            changed = self.store.upsert(game, records)
            consecutive_empty = 0
            for page in sorted(fetched, key=lambda item: item.index, reverse=True):
                if page.records:
                    break
                consecutive_empty += 1
            complete = consecutive_empty >= 2
            unavailable_ranges = list(state.unavailable_ranges)
            if complete:
                earliest = self.store.coverage(game)["earliest_draw_id"]
                if earliest is not None and int(earliest) > 1:
                    inaccessible = {
                        "start": "1".zfill(len(earliest)),
                        "end": str(int(earliest) - 1).zfill(len(earliest)),
                        "reason": "official-source-no-longer-returns-older-draws",
                    }
                    if inaccessible not in unavailable_ranges:
                        unavailable_ranges.append(inaccessible)
            updated_state = BackfillState(
                next_page_index=start_page + page_count,
                complete=complete,
                unavailable_ranges=unavailable_ranges,
            )
            self.store.save_state(game, updated_state)
            summary.fetched[game] = len(records)
            summary.changed[game] = changed
            if complete:
                summary.completed_backfills.append(game)
        return summary

    def reconcile(
        self,
        games: Iterable[str],
        *,
        recent_days: int = 14,
        max_backfill_draws: int = 250,
    ) -> CollectionSummary:
        selected = list(games)
        summary = CollectionSummary.empty()
        for game in selected:
            spec = get_game(game)
            draws_per_day = 2 if game == "lotto535" else 3 / 7
            expected = math.ceil(recent_days * draws_per_day)
            page_count = max(1, math.ceil(expected / spec.page_size) + 1)
            fetched = self._fetch_pages(get_adapter(game), range(page_count), True)
            if not any(page.records for page in fetched):
                raise ParseError(f"Reconciliation pages for {game} contained no valid draws")
            records = _deduplicate(record for page in fetched for record in page.records)
            summary.fetched[game] = len(records)
            summary.changed[game] = self.store.upsert(game, records)
        incomplete = [game for game in selected if not self.store.load_state(game).complete]
        if incomplete:
            summary.merge(
                self.backfill(
                    incomplete,
                    resume=True,
                    max_draws=max_backfill_draws,
                    audit_official_pdf=False,
                )
            )
        return summary

    def run_scheduled(self) -> CollectionSummary:
        now = local_now()
        games = due_games(now)
        if games:
            LOGGER.info("Collecting due games: %s", ", ".join(games))
            return self.collect_latest(games, audit_official_pdf=True)
        if now.hour in {2, 3, 4}:
            LOGGER.info("Running nightly reconciliation")
            return self.reconcile(GAMES)
        LOGGER.info("No game is due at %s", now.isoformat())
        return CollectionSummary.empty()

    def _fetch_pages(
        self,
        adapter: BaseAdapter,
        indexes: Iterable[int],
        audit_official_pdf: bool,
    ) -> list[FetchedPage]:
        index_list = list(indexes)
        pages: list[FetchedPage] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            page_futures = {
                pool.submit(adapter.fetch_page, self.client, index): index for index in index_list
            }
            for page_future in as_completed(page_futures):
                index = page_futures[page_future]
                response, records = page_future.result()
                pages.append(FetchedPage(index=index, response=response, records=records))
        records = _deduplicate(record for page in pages for record in page.records)
        enriched: dict[tuple[str, str], DrawRecord] = {}
        if records:
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                enrichment_futures = {
                    pool.submit(
                        self._enrich_record, adapter, record, audit_official_pdf
                    ): record.key
                    for record in records
                }
                for enrichment_future in as_completed(enrichment_futures):
                    enriched[enrichment_futures[enrichment_future]] = enrichment_future.result()
        return [
            FetchedPage(
                index=page.index,
                response=page.response,
                records=[enriched.get(record.key, record) for record in page.records],
            )
            for page in sorted(pages, key=lambda item: item.index)
        ]

    def _enrich_record(
        self,
        adapter: BaseAdapter,
        record: DrawRecord,
        audit_official_pdf: bool,
    ) -> DrawRecord:
        enriched = adapter.fetch_detail(self.client, record)
        if audit_official_pdf and enriched.source_pdf_url:
            response = self.client.get_bytes(enriched.source_pdf_url)
            enriched = audit_pdf(enriched, response.content, response.url)
        enriched.validate()
        return enriched


def _deduplicate(records: Iterable[DrawRecord]) -> list[DrawRecord]:
    unique: dict[tuple[str, str], DrawRecord] = {}
    for record in records:
        unique[record.key] = record
    return list(unique.values())
