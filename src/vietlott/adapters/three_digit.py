"""Max 3D family parser preserving leading zeroes."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from vietlott.adapters.base import (
    BaseAdapter,
    extract_date,
    extract_draw_id,
    extract_time_and_slot,
    find_pdf_url,
)
from vietlott.http import OfficialResponse
from vietlott.models import DrawRecord, ThreeDigitResult, ThreeDigitTier

TIER_LAYOUT = (
    ("special", "Giải Đặc biệt", 2),
    ("first", "Giải Nhất", 4),
    ("second", "Giải Nhì", 6),
    ("third", "Giải Ba", 8),
)


class ThreeDigitAdapter(BaseAdapter):
    def parse_page(self, response: OfficialResponse) -> list[DrawRecord]:
        assert response.html is not None
        soup = BeautifulSoup(response.html, "lxml")
        records: list[DrawRecord] = []
        containers = soup.select(".tong_day_so_ket_qua")
        for container in containers:
            parent_row = container.find_parent("tr")
            row = parent_row if isinstance(parent_row, Tag) else container
            row_text = row.get_text(" ", strip=True)
            draw_date = extract_date(row_text)
            draw_id = extract_draw_id(row_text)
            if draw_id is None:
                anchor = row.find("a")
                draw_id = extract_draw_id(
                    anchor.get_text(" ", strip=True) if anchor else "", fallback_digits=True
                )
            if draw_date is None or draw_id is None:
                continue
            values = _extract_three_digit_values(container)
            if len(values) < 20:
                continue
            values = values[:20]
            tiers: list[ThreeDigitTier] = []
            cursor = 0
            for code, name, count in TIER_LAYOUT:
                tiers.append(
                    ThreeDigitTier(code=code, name=name, numbers=values[cursor : cursor + count])
                )
                cursor += count
            draw_time, draw_slot = extract_time_and_slot(row_text, self.spec.draw_hours[0])
            record = DrawRecord(
                game=self.spec.code,
                draw_id=draw_id,
                draw_date=draw_date,
                draw_time=draw_time,
                draw_slot=draw_slot,
                result=ThreeDigitResult(tiers=tiers),
                prizes=[],
                source_url=response.url,
                source_sha256=response.sha256,
                source_pdf_url=find_pdf_url(str(row), response.url),
                retrieved_at=response.retrieved_at,
            )
            record.validate()
            records.append(record)
        return records


def _extract_three_digit_values(container: object) -> list[str]:
    assert isinstance(container, Tag)
    spans = container.find_all("span", class_="bong_tron")
    texts = [span.get_text(strip=True) for span in spans]
    if texts and all(text.isdigit() and len(text) == 1 for text in texts):
        return ["".join(texts[index : index + 3]) for index in range(0, len(texts) - 2, 3)]
    values = [text for text in texts if re.fullmatch(r"\d{3}", text)]
    if values:
        return values
    text = container.get_text(" ", strip=True)
    return re.findall(r"(?<!\d)\d{3}(?!\d)", text)
