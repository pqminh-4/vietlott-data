"""Product contracts, endpoints, and draw schedules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GameKind = Literal["number_set", "three_digit_tiers"]


@dataclass(frozen=True, slots=True)
class GameSpec:
    code: str
    display_name: str
    endpoint: str
    detail_path: str
    kind: GameKind
    page_size: int
    weekdays: frozenset[int]
    draw_hours: tuple[int, ...]
    main_count: int | None = None
    main_min: int | None = None
    main_max: int | None = None
    bonus_count: int = 0
    bonus_min: int | None = None
    bonus_max: int | None = None
    render_key: str | None = None
    array_rows: int | None = None
    array_columns: int | None = None
    game_id: str | None = None


OFFICIAL_HOSTS = frozenset({"vietlott.vn", "www.vietlott.vn", "media.vietlott.vn"})
AJAX_BASE = "https://vietlott.vn/ajaxpro"

GAMES: dict[str, GameSpec] = {
    "mega645": GameSpec(
        code="mega645",
        display_name="Mega 6/45",
        endpoint=(
            f"{AJAX_BASE}/"
            "Vietlott.PlugIn.WebParts.Game645CompareWebPart,Vietlott.PlugIn.WebParts.ashx"
        ),
        detail_path="/vi/trung-thuong/ket-qua-trung-thuong/645",
        kind="number_set",
        page_size=8,
        weekdays=frozenset({2, 4, 6}),
        draw_hours=(18,),
        main_count=6,
        main_min=1,
        main_max=45,
        render_key="8290fce2",
        array_rows=6,
        array_columns=18,
    ),
    "power655": GameSpec(
        code="power655",
        display_name="Power 6/55",
        endpoint=(
            f"{AJAX_BASE}/"
            "Vietlott.PlugIn.WebParts.Game655CompareWebPart,Vietlott.PlugIn.WebParts.ashx"
        ),
        detail_path="/vi/trung-thuong/ket-qua-trung-thuong/655",
        kind="number_set",
        page_size=8,
        weekdays=frozenset({1, 3, 5}),
        draw_hours=(18,),
        main_count=6,
        main_min=1,
        main_max=55,
        bonus_count=1,
        bonus_min=1,
        bonus_max=55,
        render_key="23bbd667",
        array_rows=5,
        array_columns=18,
    ),
    "lotto535": GameSpec(
        code="lotto535",
        display_name="Lotto 5/35",
        endpoint=(
            f"{AJAX_BASE}/"
            "Vietlott.PlugIn.WebParts.Game535CompareWebPart,Vietlott.PlugIn.WebParts.ashx"
        ),
        detail_path="/vi/trung-thuong/ket-qua-trung-thuong/535",
        kind="number_set",
        page_size=8,
        weekdays=frozenset(range(7)),
        draw_hours=(13, 21),
        main_count=5,
        main_min=1,
        main_max=35,
        bonus_count=1,
        bonus_min=1,
        bonus_max=12,
        render_key="d0ea794f",
        array_rows=5,
        array_columns=35,
    ),
    "max3d": GameSpec(
        code="max3d",
        display_name="Max 3D / Max 3D+",
        endpoint=(
            f"{AJAX_BASE}/"
            "Vietlott.PlugIn.WebParts.GameMax3DCompareWebPart,Vietlott.PlugIn.WebParts.ashx"
        ),
        detail_path="/vi/trung-thuong/ket-qua-trung-thuong/max-3D",
        kind="three_digit_tiers",
        page_size=5,
        weekdays=frozenset({0, 2, 4}),
        draw_hours=(18,),
        game_id="5",
    ),
    "max3d_pro": GameSpec(
        code="max3d_pro",
        display_name="Max 3D Pro",
        endpoint=(
            f"{AJAX_BASE}/"
            "Vietlott.PlugIn.WebParts.GameMax3DProCompareWebPart,Vietlott.PlugIn.WebParts.ashx"
        ),
        detail_path="/vi/trung-thuong/ket-qua-trung-thuong/max-3DPro",
        kind="three_digit_tiers",
        page_size=5,
        weekdays=frozenset({1, 3, 5}),
        draw_hours=(18,),
        game_id="7",
    ),
}


def get_game(code: str) -> GameSpec:
    """Return a product contract or raise a user-facing error."""
    try:
        return GAMES[code]
    except KeyError as exc:
        supported = ", ".join(GAMES)
        raise ValueError(f"Unknown game {code!r}; expected one of: {supported}") from exc


def render_info() -> dict[str, object]:
    """Public rendering context expected by Vietlott AjaxPro web parts."""
    return {
        "SiteId": "main.frontend.vi",
        "SiteAlias": "main.vi",
        "UserSessionId": "",
        "SiteLang": "vi",
        "IsPageDesign": False,
        "ExtraParam1": "",
        "ExtraParam2": "",
        "ExtraParam3": "",
        "SiteURL": "",
        "WebPage": None,
        "SiteName": "Vietlott",
        "OrgPageAlias": None,
        "PageAlias": None,
        "RefKey": None,
        "FullPageAlias": None,
        "System": 1,
    }
