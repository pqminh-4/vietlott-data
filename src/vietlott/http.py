"""Conservative HTTP access to official Vietlott sources."""

from __future__ import annotations

import json
import logging
import math
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from hashlib import sha256
from threading import Lock
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from vietlott.config import OFFICIAL_HOSTS, WEB_BASE
from vietlott.errors import FetchError, ParseError, TemporaryFetchError

LOGGER = logging.getLogger(__name__)


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
        request_interval: float = 2.0,
        rate_limit_backoff: float = 60.0,
        bootstrap_ajax_cookie: bool = True,
        relay_url: str | None = None,
        relay_token: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.retries = retries
        self.backoff_base = backoff_base
        if not math.isfinite(request_interval) or request_interval < 0:
            raise ValueError("request_interval must be finite and non-negative")
        if not math.isfinite(rate_limit_backoff) or rate_limit_backoff < 0:
            raise ValueError("rate_limit_backoff must be finite and non-negative")
        self.request_interval = request_interval
        self.rate_limit_backoff = rate_limit_backoff
        # Mọi luồng dùng chung một cổng request để tránh gửi dồn khi làm giàu dữ liệu.
        self._request_lock = Lock()
        self._next_request_at = 0.0
        self._rate_limit_count = 0
        self.bootstrap_ajax_cookie = bootstrap_ajax_cookie
        relay_setting = relay_url if relay_url is not None else os.getenv("VIETLOTT_RELAY_URL", "")
        self.relay_url = relay_setting.rstrip("/")
        self.relay_token = relay_token or os.getenv("VIETLOTT_RELAY_TOKEN", "")
        if bool(self.relay_url) != bool(self.relay_token):
            raise ValueError(
                "VIETLOTT_RELAY_URL and VIETLOTT_RELAY_TOKEN must be configured together"
            )
        if self.relay_url:
            parsed_relay = urlparse(self.relay_url)
            if (
                parsed_relay.scheme != "https"
                or not parsed_relay.hostname
                or parsed_relay.query
                or parsed_relay.fragment
            ):
                raise ValueError("VIETLOTT_RELAY_URL must be a plain HTTPS URL")
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

    def post_ajax(
        self, url: str, body: dict[str, Any], *, detail: bool = False
    ) -> OfficialResponse:
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
                "Origin": WEB_BASE,
                "Referer": (f"{WEB_BASE}/vi/trung-thuong/ket-qua-trung-thuong/winning-number-645"),
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
        if not isinstance(value, dict) or value.get("Error"):
            raise ParseError("Vietlott AjaxPro returned an invalid or failed result")
        html: str
        if detail:
            left, right = value.get("RetExtraParam1"), value.get("RetExtraParam2")
            if (
                not isinstance(left, str)
                or not left.strip()
                or not isinstance(right, str)
                or not right.strip()
            ):
                raise ParseError("Vietlott detail response omitted result or prize HTML")
            if value.get("RetExtraParam3") != body.get("DrawId"):
                raise ParseError("Vietlott detail response did not match requested draw ID")
            # Giữ nguyên hai phần HTML mà giao diện chính thức đặt vào các vùng này.
            html = f'<div id="divLeftContent">{left}</div><div id="divRightContent">{right}</div>'
        else:
            list_html = value.get("HtmlContent")
            if not isinstance(list_html, str):
                raise ParseError("Vietlott AjaxPro response did not include value.HtmlContent")
            html = list_html
        return OfficialResponse(
            url=self._source_url(response, url),
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
            response = self._request("GET", f"{WEB_BASE}/ajaxpro/")
            match = re.search(r'document\.cookie\s*=\s*["\']([^"\']+)', response.text)
            if match:
                pair = match.group(1).split(";", 1)[0]
                if "=" not in pair:
                    raise ParseError("Vietlott AjaxPro bootstrap returned a malformed cookie")
                name, value = pair.split("=", 1)
                cookie_domain = (
                    urlparse(self.relay_url).hostname if self.relay_url else ".vietlott.vn"
                )
                assert cookie_domain is not None
                self.client.cookies.set(name, value, domain=cookie_domain, path="/")
            self._ajax_cookie_ready = True

    def get_bytes(self, url: str) -> OfficialResponse:
        response = self._request("GET", url)
        return OfficialResponse(
            url=self._source_url(response, url),
            content=response.content,
            retrieved_at=_utc_now(),
        )

    def get_html(self, url: str) -> OfficialResponse:
        response = self._request("GET", url)
        return OfficialResponse(
            url=self._source_url(response, url),
            content=response.content,
            retrieved_at=_utc_now(),
            html=response.text,
        )

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if not _is_approved_official_url(url):
            raise FetchError(f"Refusing non-official Vietlott URL: {url}")
        request_url = url
        if self.relay_url:
            request_url = f"{self.relay_url}/proxy?{urlencode({'url': url})}"
            headers = dict(kwargs.pop("headers", {}))
            headers["Authorization"] = f"Bearer {self.relay_token}"
            kwargs["headers"] = headers

        last_error: Exception | None = None
        max_attempts = self.retries + 1
        for attempt in range(max_attempts):
            rate_limited = False
            try:
                with self._request_lock:
                    delay = self._next_request_at - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                    try:
                        response = self.client.request(method, request_url, **kwargs)
                    finally:
                        self._next_request_at = time.monotonic() + self.request_interval
                    if response.status_code == 429:
                        rate_limited = True
                        self._rate_limit_count += 1
                        retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
                        cooldown = (
                            retry_after
                            if retry_after is not None
                            else min(
                                self.rate_limit_backoff * (2 ** min(self._rate_limit_count - 1, 3)),
                                300.0,
                            )
                        )
                        # Cập nhật thời gian chờ trước khi mở khóa, kể cả ở lần thử cuối.
                        self._next_request_at = max(
                            self._next_request_at, time.monotonic() + cooldown
                        )
                        LOGGER.warning("Official source rate limited; pausing for %.1fs", cooldown)
                    elif response.is_success:
                        self._rate_limit_count = 0
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code == 403:
                    raise FetchError(
                        f"Official Vietlott source rejected the request with HTTP "
                        f"{response.status_code}"
                    )
                if response.is_success:
                    if not self.relay_url and not _is_approved_official_url(str(response.url)):
                        raise FetchError(
                            "Official Vietlott request redirected outside the allowlist"
                        )
                    return response
                if response.status_code not in {408, 425, 429, 500, 502, 503, 504}:
                    raise FetchError(
                        f"Official Vietlott source returned HTTP {response.status_code}"
                    )
                last_error = FetchError(f"Transient HTTP {response.status_code}")
            if attempt + 1 < max_attempts and not rate_limited:
                delay = self.backoff_base * (2**attempt) + random.uniform(0.0, 0.35)
                time.sleep(delay)
        raise TemporaryFetchError(
            f"Official Vietlott request failed after {max_attempts} attempts: {last_error}"
        ) from last_error

    def _source_url(self, response: httpx.Response, requested_url: str) -> str:
        if not self.relay_url:
            return str(response.url)
        reported_url = response.headers.get("X-Vietlott-Source-Url")
        source_url = str(reported_url) if reported_url else requested_url
        if not _is_approved_official_url(source_url):
            raise FetchError("Relay reported a source URL outside the Vietlott allowlist")
        return source_url


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _is_approved_official_url(value: str) -> bool:
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname in OFFICIAL_HOSTS
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
    )


def _retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        delay = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                return None
            delay = (retry_at - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    if not math.isfinite(delay) or delay < 0:
        return None
    # Retry-After có thể là HTTP-date; không rút ngắn thời hạn nguồn yêu cầu.
    return delay
