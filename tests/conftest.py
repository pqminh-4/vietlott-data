from __future__ import annotations

import json
from pathlib import Path

import pytest

from vietlott.http import OfficialResponse

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def official_response():
    def factory(name: str, endpoint: str) -> OfficialResponse:
        content = (FIXTURES / f"{name}.json").read_bytes()
        payload = json.loads(content)
        return OfficialResponse(
            url=endpoint,
            content=content,
            retrieved_at="2026-08-01T00:00:00+00:00",
            html=payload["value"]["HtmlContent"],
        )

    return factory
