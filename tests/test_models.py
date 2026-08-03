from __future__ import annotations

from dataclasses import replace

import pytest

from vietlott.errors import ValidationError
from vietlott.models import DrawRecord, NumberSetResult, ThreeDigitResult, ThreeDigitTier


def number_record() -> DrawRecord:
    return DrawRecord(
        game="mega645",
        draw_id="00001",
        draw_date="2026-07-31",
        draw_time="18:00:00",
        draw_slot="evening",
        result=NumberSetResult(main_numbers=[1, 2, 3, 4, 5, 6]),
        source_url="https://vietlott.vn/ajaxpro/result.ashx",
        source_sha256="a" * 64,
        retrieved_at="2026-08-01T00:00:00+00:00",
    )


def test_round_trip() -> None:
    original = number_record()
    assert DrawRecord.from_dict(original.to_dict()) == original


def test_duplicate_and_out_of_range_numbers_are_rejected() -> None:
    with pytest.raises(ValidationError):
        replace(
            number_record(),
            result=NumberSetResult(main_numbers=[1, 1, 3, 4, 5, 46]),
        ).validate()


def test_non_official_source_is_rejected() -> None:
    with pytest.raises(ValidationError):
        replace(number_record(), source_url="https://example.com/result").validate()


def test_draw_time_and_slot_must_be_consistent() -> None:
    with pytest.raises(ValidationError):
        replace(
            number_record(), draw_time="13:00:00", draw_slot="afternoon"
        ).validate()
    with pytest.raises(ValidationError):
        replace(number_record(), draw_time=None).validate()


@pytest.mark.parametrize("retrieved_at", ["not-a-date", "2026-08-01T00:00:00"])
def test_retrieved_at_requires_an_offset_timestamp(retrieved_at: str) -> None:
    with pytest.raises(ValidationError):
        replace(number_record(), retrieved_at=retrieved_at).validate()


def test_three_digit_strings_keep_zero_prefix() -> None:
    record = DrawRecord(
        game="max3d",
        draw_id="00001",
        draw_date="2026-07-31",
        result=ThreeDigitResult(
            tiers=[ThreeDigitTier(code="special", name="Giải Đặc biệt", numbers=["007"])]
        ),
        source_url="https://vietlott.vn/ajaxpro/result.ashx",
        source_sha256="b" * 64,
        retrieved_at="2026-08-01T00:00:00+00:00",
    )
    assert DrawRecord.from_dict(record.to_dict()).to_dict()["result"]["tiers"][0]["numbers"] == [
        "007"
    ]
