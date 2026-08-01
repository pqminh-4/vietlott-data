"""Shared parsing and request behavior for AjaxPro product web parts."""

from __future__ import annotations

import re
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import replace
from datetime import datetime
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from vietlott.config import OFFICIAL_HOSTS, WEB_BASE, GameSpec, render_info
from vietlott.errors import ParseError
from vietlott.http import OfficialResponse, VietlottClient
from vietlott.models import DrawRecord, Prize

DATE_DMY_RE = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)")
DATE_ISO_RE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
TIME_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[:hH]\s*(\d{2})(?!\d)")
DRAW_LABEL_RE = re.compile(r"(?:kỳ\s*(?:quay\s*)?(?:thưởng)?|#)\s*#?\s*(\d{1,10})", re.I)


class BaseAdapter(ABC):
    def __init__(self, spec: GameSpec) -> None:
        self.spec = spec

    def request_body(self, page_index: int, draw_id: str = "") -> dict[str, Any]:
        if self.spec.kind == "number_set":
            assert self.spec.array_rows is not None
            assert self.spec.array_columns is not None
            return {
                "ORenderInfo": render_info(),
                "Key": self.spec.render_key,
                "GameDrawId": draw_id,
                "ArrayNumbers": [
                    ["" for _ in range(self.spec.array_columns)]
                    for _ in range(self.spec.array_rows)
                ],
                "CheckMulti": False,
                "PageIndex": page_index,
            }
        return {
            "ORenderInfo": render_info(),
            "GameId": self.spec.game_id,
            "GameDrawId": draw_id,
            "PageIndex": page_index,
            "CheckMulti": 0,
            "number01": "123",
            "number02": "321",
        }

    def fetch_page(
        self, client: VietlottClient, page_index: int
    ) -> tuple[OfficialResponse, list[DrawRecord]]:
        response = client.post_ajax(self.spec.endpoint, self.request_body(page_index))
        return response, self.parse_page(response)

    def fetch_detail(self, client: VietlottClient, record: DrawRecord) -> DrawRecord:
        query = urlencode({"id": record.draw_id, "nocatche": "1"})
        detail_url = f"{WEB_BASE}{self.spec.detail_path}?{query}"
        response = client.get_html(detail_url)
        assert response.html is not None
        soup = BeautifulSoup(response.html, "lxml")
        detail_text = soup.get_text(" ", strip=True)
        detail_draw_id = extract_draw_id(detail_text)
        detail_date = extract_date(detail_text)
        if detail_draw_id is not None and detail_draw_id != record.draw_id:
            raise ParseError(f"Official detail page id did not match requested draw {record.key}")
        if detail_date is not None and detail_date != record.draw_date:
            raise ParseError(f"Official detail page date did not match requested draw {record.key}")
        parsed = self.parse_page(response)
        matching = [item for item in parsed if item.draw_id == record.draw_id]
        if parsed and not matching:
            raise ParseError(f"Official detail response did not match requested draw {record.key}")
        if matching:
            candidate = matching[0]
            if candidate.draw_date != record.draw_date or candidate.result != record.result:
                raise ParseError(f"Official list/detail mismatch for draw {record.key}")
        prizes = parse_prizes(response.html)
        pdf_url = find_pdf_url(response.html, response.url) or record.source_pdf_url
        return replace(
            record,
            prizes=prizes or record.prizes,
            source_url=response.url,
            source_sha256=response.sha256,
            retrieved_at=response.retrieved_at,
            source_pdf_url=pdf_url,
        )

    @abstractmethod
    def parse_page(self, response: OfficialResponse) -> list[DrawRecord]:
        raise NotImplementedError


def extract_date(text: str) -> str | None:
    if match := DATE_DMY_RE.search(text):
        day, month, year = (int(part) for part in match.groups())
        return datetime(year, month, day).date().isoformat()
    if match := DATE_ISO_RE.search(text):
        return match.group(0)
    return None


def extract_time_and_slot(
    text: str, default_hour: int | None = None
) -> tuple[str | None, str | None]:
    match = TIME_RE.search(text)
    if match:
        hour, minute = (int(part) for part in match.groups())
    elif default_hour is not None:
        hour, minute = default_hour, 0
    else:
        return None, None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None, None
    slot = {13: "afternoon", 18: "evening", 21: "night"}.get(hour, f"hour-{hour:02d}")
    return f"{hour:02d}:{minute:02d}:00", slot


def extract_draw_id(text: str, *, fallback_digits: bool = False) -> str | None:
    if match := DRAW_LABEL_RE.search(text):
        return normalize_draw_id(match.group(1))
    if fallback_digits:
        compact = text.strip().lstrip("#").strip()
        if compact.isdigit() and len(compact) <= 10:
            return normalize_draw_id(compact)
    return None


def normalize_draw_id(value: str) -> str:
    return value.strip().lstrip("#").zfill(5)


def parse_prizes(html: str) -> list[Prize]:
    """Parse prize tables when a list or detail response exposes them."""
    soup = BeautifulSoup(html, "lxml")
    prizes: list[Prize] = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        header_position: int | None = None
        prize_idx: int | None = None
        amount_idx: int | None = None
        winner_idx: int | None = None
        for position, candidate in enumerate(rows):
            headers = [
                _fold(cell.get_text(" ", strip=True)) for cell in candidate.find_all(["th", "td"])
            ]
            candidate_prize = _header_index(
                headers, lambda value: "giai" in value and "so luong" not in value
            )
            candidate_amount = _header_index(
                headers, lambda value: "gia tri" in value or "muc thuong" in value
            )
            candidate_winners = _header_index(
                headers, lambda value: "so luong" in value or "so ve" in value
            )
            if candidate_prize is not None and (
                candidate_amount is not None or candidate_winners is not None
            ):
                header_position = position
                prize_idx = candidate_prize
                amount_idx = candidate_amount
                winner_idx = candidate_winners
                break
        if header_position is None or prize_idx is None:
            continue
        for row in rows[header_position + 1 :]:
            cells = row.find_all(["th", "td"])
            if prize_idx >= len(cells):
                continue
            name = cells[prize_idx].get_text(" ", strip=True)
            folded_name = _fold(name)
            if not name or "giai" not in folded_name and "jackpot" not in folded_name:
                continue
            amount = _integer_from_cell(cells, amount_idx)
            winners = _integer_from_cell(cells, winner_idx)
            is_jackpot = "jackpot" in folded_name or "doc dac" in folded_name
            prizes.append(
                Prize(
                    code=_slug(name),
                    name=name,
                    amount_vnd=None if is_jackpot else amount,
                    winner_count=winners,
                    jackpot_vnd=amount if is_jackpot else None,
                )
            )
    deduplicated: dict[str, Prize] = {}
    for prize in prizes:
        deduplicated[prize.code] = prize
    return list(deduplicated.values())


def find_pdf_url(html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    for anchor in soup.find_all("a", href=True):
        assert isinstance(anchor, Tag)
        href = str(anchor.get("href"))
        if ".pdf" not in href.lower():
            continue
        url = urljoin(base_url, href)
        parsed = urlparse(url)
        if parsed.scheme == "https" and parsed.hostname in OFFICIAL_HOSTS:
            return url
    return None


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.lower().replace("đ", "d"))
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", _fold(value)).strip("-")
    return slug or "prize"


def _header_index(headers: list[str], predicate: Any) -> int | None:
    for index, header in enumerate(headers):
        if predicate(header):
            return index
    return None


def _integer_from_cell(cells: list[Tag], index: int | None) -> int | None:
    if index is None or index >= len(cells):
        return None
    digits = re.sub(r"\D", "", cells[index].get_text(" ", strip=True))
    return int(digits) if digits else None
