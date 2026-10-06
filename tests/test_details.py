from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import httpx
import pytest
from bs4 import BeautifulSoup

from vietlott.adapters import get_adapter
from vietlott.config import get_game
from vietlott.errors import ParseError
from vietlott.http import VietlottClient
from vietlott.models import DrawRecord, NumberSetResult, ThreeDigitResult, ThreeDigitTier

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = {
    "mega645": ("01571", "2026-10-04", [15, 20, 29, 37, 40, 45], [], 4),
    "power655": ("01407", "2026-10-06", [6, 7, 18, 20, 24, 27], [1], 5),
    "lotto535": ("00930", "2026-10-06", [15, 22, 27, 28, 29], [3], 7),
    "max3d": (
        "01141",
        "2026-10-05",
        [
            "546",
            "085",
            "472",
            "486",
            "159",
            "586",
            "525",
            "877",
            "261",
            "110",
            "123",
            "238",
            "399",
            "183",
            "166",
            "713",
            "699",
            "490",
            "993",
            "152",
        ],
        [],
        7,
    ),
    "max3d_pro": (
        "00788",
        "2026-10-06",
        [
            "038",
            "091",
            "232",
            "504",
            "975",
            "346",
            "989",
            "690",
            "066",
            "206",
            "904",
            "115",
            "610",
            "570",
            "216",
            "577",
            "913",
            "985",
            "577",
            "286",
        ],
        [],
        8,
    ),
}


def expected_record(game: str) -> DrawRecord:
    draw_id, draw_date, numbers, bonus, _ = EXPECTED[game]
    if get_game(game).kind == "number_set":
        result = NumberSetResult(main_numbers=numbers, bonus_numbers=bonus)
    else:
        result = ThreeDigitResult(
            tiers=[
                ThreeDigitTier("special", "Giải Đặc biệt", numbers[:2]),
                ThreeDigitTier("first", "Giải Nhất", numbers[2:6]),
                ThreeDigitTier("second", "Giải Nhì", numbers[6:12]),
                ThreeDigitTier("third", "Giải Ba", numbers[12:]),
            ]
        )
    return DrawRecord(
        game=game,
        draw_id=draw_id,
        draw_date=draw_date,
        result=result,
        source_url=get_game(game).endpoint,
        source_sha256="a" * 64,
        retrieved_at="2026-10-06T00:00:00+00:00",
    )


@pytest.mark.parametrize("game", list(EXPECTED))
def test_official_detail_api_preserves_results_prizes_and_provenance(game: str) -> None:
    content = (FIXTURES / f"{game}-detail.json").read_bytes()
    spec = get_game(game)
    record = expected_record(game)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == spec.detail_endpoint
        assert request.headers["X-AjaxPro-Method"] == "ServerSideDrawResult"
        body = json.loads(request.content)
        assert body["DrawId"] == record.draw_id
        assert body["Key"] == spec.detail_render_key
        assert body["ORenderInfo"]["SiteId"] == "main.frontend.vi"
        return httpx.Response(200, content=content, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as raw:
        client = VietlottClient(client=raw, bootstrap_ajax_cookie=False)
        enriched = get_adapter(game).fetch_detail(client, record)
    assert enriched.result == record.result
    assert len(enriched.prizes) == EXPECTED[game][4]
    assert enriched.source_url == spec.detail_endpoint
    assert enriched.source_sha256 == sha256(content).hexdigest()
    enriched.validate()
    if game == "power655":
        assert enriched.prizes[0].jackpot_vnd == 122_467_126_800
        assert enriched.prizes[1].winner_count == 1


@pytest.mark.parametrize("change", ["id", "date", "result"])
def test_detail_must_match_the_independent_list_record(change: str) -> None:
    content = (FIXTURES / "mega645-detail.json").read_bytes()
    record = expected_record("mega645")
    if change == "id":
        record = replace(record, draw_id="01570")
    elif change == "date":
        record = replace(record, draw_date="2026-10-03")
    else:
        record = replace(record, result=NumberSetResult(main_numbers=[1, 2, 3, 4, 5, 6]))
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, request=request, content=content)
        )
    ) as raw:
        client = VietlottClient(client=raw, bootstrap_ajax_cookie=False)
        with pytest.raises(ParseError, match="match"):
            get_adapter("mega645").fetch_detail(client, record)


@pytest.mark.parametrize("change", ["id", "date", "missing-id", "missing-date"])
def test_detail_html_identity_is_required_even_when_api_metadata_matches(change: str) -> None:
    payload = json.loads((FIXTURES / "mega645-detail.json").read_bytes())
    left = payload["value"]["RetExtraParam1"]
    if change.endswith("id"):
        left = left.replace("#01571", "#01570" if change == "id" else "")
    else:
        left = left.replace("04/10/2026", "03/10/2026" if change == "date" else "")
    payload["value"]["RetExtraParam1"] = left
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, request=request, json=payload)
        )
    ) as raw:
        client = VietlottClient(client=raw, bootstrap_ajax_cookie=False)
        with pytest.raises(ParseError, match="match"):
            get_adapter("mega645").fetch_detail(client, expected_record("mega645"))


@pytest.mark.parametrize("game", list(EXPECTED))
@pytest.mark.parametrize("change", ["missing", "extra"])
def test_detail_rejects_missing_or_extra_winning_numbers(game: str, change: str) -> None:
    payload = json.loads((FIXTURES / f"{game}-detail.json").read_bytes())
    html = f'<div id="divLeftContent">{payload["value"]["RetExtraParam1"]}</div>'
    if change == "extra":
        html = html.replace("</div>", '<span class="bong_tron">1</span></div>', 1)
    else:
        soup = BeautifulSoup(html, "lxml")
        soup.select_one("span.bong_tron").decompose()
        html = str(soup)
    with pytest.raises(ParseError):
        get_adapter(game).parse_detail_result(html)
