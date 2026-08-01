"""Conservative HTTP access to official Vietlott sources."""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from datetime import UTC
from hashlib import sha256
from threading import Lock
from typing import Any

import httpx

from vietlott.errors import FetchError, ParseError


@dataclass(frozen=True, slots=True)
class OfficialResponse:
    url: str
    content: bytes
    retrieved_at: str
    html: str | None = None

    @property
    def sha256(self) -> str:
        return sha256(self.content).hexdigest()


class VietlottClient:
    """Retrying client that never treats an access-denied page as valid data."""

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        retries: int = 3,
        backoff_base: float = 0.75,
        bootstrap_ajax_cookie: bool = True,
        client: httpx.Client | None = None,
    ) -> None:
        self.retries = retries
        self.backoff_base = backoff_base
        self.bootstrap_ajax_cookie = bootstrap_ajax_cookie
        self._ajax_cookie_ready = False
        self._ajax_cookie_lock = Lock()
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
                ),
                "Accept": "*/*",
                "Accept-Language": "vi,en-US;q=0.7,en;q=0.5",
            },
        )

    def __enter__(self) -> VietlottClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def post_ajax(self, url: str, body: dict[str, Any]) -> OfficialResponse:
        self._ensure_ajax_cookie()
        content = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        response = self._request(
            "POST",
            url,
            content=content,
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "X-AjaxPro-Method": "ServerSideDrawResult",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": "https://vietlott.vn",
                "Referer": (
                    "https://vietlott.vn/vi/trung-thuong/ket-qua-trung-thuong/winning-number-645"
                ),
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ParseError("Vietlott AjaxPro response was not JSON") from exc
        if not isinstance(payload, dict) or payload.get("error"):
            raise ParseError(f"Vietlott AjaxPro returned an error: {payload!r}")
        value = payload.get("value")
        html = value.get("HtmlContent") if isinstance(value, dict) else None
        if not isinstance(html, str):
            raise ParseError("Vietlott AjaxPro response did not include value.HtmlContent")
        return OfficialResponse(
            url=str(response.url),
            content=response.content,
            retrieved_at=_utc_now(),
            html=html,
        )

    def _ensure_ajax_cookie(self) -> None:
        """Load Vietlott's first-party JavaScript cookie when its edge requires one."""
        if not self.bootstrap_ajax_cookie or self._ajax_cookie_ready:
            return
        with self._ajax_cookie_lock:
            if self._ajax_cookie_ready:
                return
            response = self._request("GET", "https://vietlott.vn/ajaxpro/")
            match = re.search(r'document\.cookie\s*=\s*["\']([^"\']+)', response.text)
            if match:
                pair = match.group(1).split(";", 1)[0]
                if "=" not in pair:
                    raise ParseError("Vietlott AjaxPro bootstrap returned a malformed cookie")
                name, value = pair.split("=", 1)
                self.client.cookies.set(name, value, domain="vietlott.vn", path="/")
            self._ajax_cookie_ready = True

    def get_bytes(self, url: str) -> OfficialResponse:
        response = self._request("GET", url)
        return OfficialResponse(
            url=str(response.url),
            content=response.content,
            retrieved_at=_utc_now(),
        )

    def get_html(self, url: str) -> OfficialResponse:
        response = self._request("GET", url)
        return OfficialResponse(
            url=str(response.url),
            content=response.content,
            retrieved_at=_utc_now(),
            html=response.text,
        )

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        max_attempts = self.retries + 1
        for attempt in range(max_attempts):
            try:
                response = self.client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code in {403, 429}:
                    raise FetchError(
                        f"Official Vietlott source rejected the request with HTTP "
                        f"{response.status_code}"
                    )
                if response.is_success:
                    return response
                if response.status_code not in {408, 425, 500, 502, 503, 504}:
                    raise FetchError(
                        f"Official Vietlott source returned HTTP {response.status_code}"
                    )
                last_error = FetchError(f"Transient HTTP {response.status_code}")
            if attempt + 1 < max_attempts:
                delay = self.backoff_base * (2**attempt) + random.uniform(0.0, 0.35)
                time.sleep(delay)
        raise FetchError(
            f"Official Vietlott request failed after {max_attempts} attempts"
        ) from last_error


def _utc_now() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat(timespec="seconds")
