"""Mega 6/45, Power 6/55, and Lotto 5/35 parser."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from vietlott.adapters.base import (
    BaseAdapter,
    extract_date,
    extract_draw_id,
    extract_time_and_slot,
    find_pdf_url,
)
from vietlott.http import OfficialResponse
from vietlott.models import DrawRecord, NumberSetResult


class NumberSetAdapter(BaseAdapter):
    def parse_page(self, response: OfficialResponse) -> list[DrawRecord]:
        assert response.html is not None
        soup = BeautifulSoup(response.html, "lxml")
        records: list[DrawRecord] = []
        total_count = (self.spec.main_count or 0) + self.spec.bonus_count
        for row in soup.select("table tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 3:
                cells = row.find_all("td")
            if len(cells) < 3:
                continue
            draw_date = extract_date(cells[0].get_text(" ", strip=True))
            draw_id = extract_draw_id(cells[1].get_text(" ", strip=True), fallback_digits=True)
            if draw_date is None or draw_id is None:
                continue
            result_cell = cells[2]
            span_values = [
                span.get_text(" ", strip=True)
                for span in result_cell.find_all("span")
                if span.get_text(" ", strip=True) != "|"
            ]
            numbers = [int(value) for value in span_values if value.isdigit()]
            if len(numbers) < total_count:
                numbers = [
                    int(value)
                    for value in re.findall(r"(?<!\d)\d{1,2}(?!\d)", result_cell.get_text(" "))
                ]
            if len(numbers) < total_count:
                continue
            numbers = numbers[:total_count]
            row_text = row.get_text(" ", strip=True)
            default_hour = self.spec.draw_hours[0] if len(self.spec.draw_hours) == 1 else None
            draw_time, draw_slot = extract_time_and_slot(row_text, default_hour)
            record = DrawRecord(
                game=self.spec.code,
                draw_id=draw_id,
                draw_date=draw_date,
                draw_time=draw_time,
                draw_slot=draw_slot,
                result=NumberSetResult(
                    main_numbers=numbers[: self.spec.main_count],
                    bonus_numbers=numbers[self.spec.main_count :],
                ),
                prizes=[],
                source_url=response.url,
                source_sha256=response.sha256,
                source_pdf_url=find_pdf_url(str(row), response.url),
                retrieved_at=response.retrieved_at,
            )
            record.validate()
            records.append(record)
        return _assign_lotto_slots(records) if self.spec.code == "lotto535" else records


def _assign_lotto_slots(records: list[DrawRecord]) -> list[DrawRecord]:
    """Fill slots only when a response clearly has both daily Lotto draws."""
    by_date: dict[str, list[DrawRecord]] = {}
    for record in records:
        by_date.setdefault(record.draw_date, []).append(record)
    for daily in by_date.values():
        unresolved = [record for record in daily if record.draw_time is None]
        if len(unresolved) != 2:
            continue
        unresolved.sort(key=lambda record: int(record.draw_id))
        unresolved[0].draw_time, unresolved[0].draw_slot = "13:00:00", "afternoon"
        unresolved[1].draw_time, unresolved[1].draw_slot = "21:00:00", "night"
    return records
