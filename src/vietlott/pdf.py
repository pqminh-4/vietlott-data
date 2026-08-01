"""Optional audit of an Ajax result against an official Vietlott PDF."""

from __future__ import annotations

import re
from dataclasses import replace
from hashlib import sha256
from io import BytesIO

from pypdf import PdfReader

from vietlott.errors import ParseError
from vietlott.models import DrawRecord, NumberSetResult, ThreeDigitResult


def audit_pdf(record: DrawRecord, content: bytes, url: str) -> DrawRecord:
    try:
        reader = PdfReader(BytesIO(content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise ParseError(f"Could not read official PDF for {record.key}") from exc
    compact = re.sub(r"\s+", " ", text)
    if record.draw_id.lstrip("0") not in compact and record.draw_id not in compact:
        raise ParseError(f"Official PDF draw id did not match {record.key}")
    dmy = "{2}/{1}/{0}".format(*record.draw_date.split("-"))
    if dmy not in compact:
        raise ParseError(f"Official PDF date did not match {record.key}")
    expected: list[str]
    if isinstance(record.result, NumberSetResult):
        expected = [f"{number:02d}" for number in record.result.main_numbers]
        expected.extend(f"{number:02d}" for number in record.result.bonus_numbers)
    elif isinstance(record.result, ThreeDigitResult):
        expected = [number for tier in record.result.tiers for number in tier.numbers]
    else:  # pragma: no cover - exhaustive guard
        expected = []
    missing = [value for value in expected if value not in compact]
    if missing:
        raise ParseError(f"Official PDF did not contain all results for {record.key}: {missing}")
    compact_digits = re.sub(r"\D", "", compact)
    # Vietlott result PDFs expose the rolling jackpot, but generally omit the
    # fixed prize tiers that are present in the official HTML detail table.
    prize_values = {prize.jackpot_vnd for prize in record.prizes if prize.jackpot_vnd is not None}
    missing_prize_values = [
        value for value in sorted(prize_values) if str(value) not in compact_digits
    ]
    if missing_prize_values:
        raise ParseError(
            f"Official PDF did not contain all prize values for {record.key}: "
            f"{missing_prize_values}"
        )
    return replace(
        record,
        source_pdf_url=url,
        source_pdf_sha256=sha256(content).hexdigest(),
    )
