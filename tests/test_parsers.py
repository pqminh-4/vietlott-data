from __future__ import annotations

from pathlib import Path

import pytest

from vietlott.adapters import get_adapter
from vietlott.adapters.base import parse_prizes
from vietlott.adapters.number_set import _assign_lotto_slots
from vietlott.config import get_game
from vietlott.errors import ParseError
from vietlott.http import OfficialResponse
from vietlott.models import DrawRecord, NumberSetResult, ThreeDigitResult
from vietlott.pdf import audit_pdf


@pytest.mark.parametrize(
    ("game", "fixture", "count"),
    [
        ("mega645", "mega645", 1),
        ("power655", "power655", 1),
        ("lotto535", "lotto535", 2),
        ("max3d", "max3d", 1),
        ("max3d_pro", "max3d_pro", 1),
    ],
)
def test_product_fixtures(official_response, game: str, fixture: str, count: int) -> None:
    adapter = get_adapter(game)
    records = adapter.parse_page(official_response(fixture, get_game(game).endpoint))
    assert len(records) == count
    assert all(record.game == game for record in records)
    assert all(record.to_dict()["schema_version"] == "1.0" for record in records)


def test_number_set_and_lotto_slots(official_response) -> None:
    power = get_adapter("power655").parse_page(
        official_response("power655", get_game("power655").endpoint)
    )[0]
    assert isinstance(power.result, NumberSetResult)
    assert power.result.main_numbers == [2, 12, 24, 28, 43, 49]
    assert power.result.bonus_numbers == [51]

    lotto = get_adapter("lotto535").parse_page(
        official_response("lotto535", get_game("lotto535").endpoint)
    )
    assert [record.draw_time for record in lotto] == ["13:00:00", "21:00:00"]
    assert [record.draw_slot for record in lotto] == ["afternoon", "night"]


def _lotto_record(draw_id: str, draw_date: str, slot: str | None = None) -> DrawRecord:
    draw_time = {"afternoon": "13:00:00", "night": "21:00:00"}.get(slot)
    return DrawRecord(
        game="lotto535",
        draw_id=draw_id,
        draw_date=draw_date,
        draw_time=draw_time,
        draw_slot=slot,
        result=NumberSetResult(main_numbers=[1, 2, 3, 4, 5], bonus_numbers=[6]),
        source_url=get_game("lotto535").endpoint,
        source_sha256="a" * 64,
        retrieved_at="2026-08-03T00:00:00+07:00",
    )


def test_lotto_slot_is_inferred_across_page_and_day_boundaries() -> None:
    records = [
        _lotto_record("00794", "2026-07-31"),
        _lotto_record("00795", "2026-08-01", "afternoon"),
        _lotto_record("00800", "2026-08-02", "night"),
        _lotto_record("00801", "2026-08-03"),
    ]
    assigned = _assign_lotto_slots(records)
    assert [(item.draw_id, item.draw_slot) for item in assigned] == [
        ("00794", "night"),
        ("00795", "afternoon"),
        ("00800", "night"),
        ("00801", "afternoon"),
    ]


def test_lotto_slot_is_not_guessed_for_non_consecutive_ids() -> None:
    records = [
        _lotto_record("00800", "2026-08-02", "night"),
        _lotto_record("00802", "2026-08-03"),
        _lotto_record("00804", "2026-08-03"),
    ]
    assigned = _assign_lotto_slots(records)
    assert [item.draw_slot for item in assigned] == ["night", None, None]


def test_max3d_preserves_leading_zeroes(official_response) -> None:
    record = get_adapter("max3d_pro").parse_page(
        official_response("max3d_pro", get_game("max3d_pro").endpoint)
    )[0]
    assert isinstance(record.result, ThreeDigitResult)
    assert record.result.tiers[0].numbers == ["096", "219"]
    assert len([number for tier in record.result.tiers for number in tier.numbers]) == 20


def test_prize_table() -> None:
    html = (Path(__file__).parent / "fixtures" / "prizes.html").read_text(encoding="utf-8")
    prizes = parse_prizes(html)
    assert prizes[0].jackpot_vnd == 43_137_012_450
    assert prizes[0].winner_count == 0
    assert prizes[1].amount_vnd == 40_000_000
    assert prizes[1].winner_count == 12


def test_lotto_prize_header_can_follow_jackpot_banner() -> None:
    html = """
    <table>
      <tr><td>Giải Độc Đắc 8.374.067.500 VND</td></tr>
      <tr><th>Giải thưởng</th><th>Kết quả</th><th>Số lượng giải</th><th>Giá trị giải</th></tr>
      <tr><td>Giải Độc Đắc</td><td>O O O O O + O</td><td>0</td><td>8.374.067.500</td></tr>
    </table>
    """
    prize = parse_prizes(html)[0]
    assert prize.jackpot_vnd == 8_374_067_500
    assert prize.amount_vnd is None


def test_empty_and_malformed_sources_are_not_records() -> None:
    response = OfficialResponse(
        url=get_game("mega645").endpoint,
        content=b"{}",
        retrieved_at="2026-08-01T00:00:00+00:00",
        html="<html><p>unexpected</p></html>",
    )
    assert get_adapter("mega645").parse_page(response) == []


def test_malformed_pdf_is_rejected(official_response) -> None:
    record = get_adapter("mega645").parse_page(
        official_response("mega645", get_game("mega645").endpoint)
    )[0]
    with pytest.raises(ParseError):
        audit_pdf(record, b"not a PDF", "https://media.vietlott.vn/result.pdf")
