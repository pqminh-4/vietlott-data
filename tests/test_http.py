from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest

from vietlott.errors import FetchError, ParseError, TemporaryFetchError
from vietlott.http import VietlottClient, _retry_after_seconds


def test_concurrent_requests_share_pacing_and_rate_limit_cooldown() -> None:
    clock = [0.0]
    requested_at: list[float] = []

    def sleep(delay: float) -> None:
        clock[0] += delay

    def handler(request: httpx.Request) -> httpx.Response:
        requested_at.append(clock[0])
        return httpx.Response(429 if len(requested_at) == 1 else 200, request=request, text="ok")

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as raw_client,
        patch("vietlott.http.time.monotonic", side_effect=lambda: clock[0]),
        patch("vietlott.http.time.sleep", side_effect=sleep),
    ):
        client = VietlottClient(client=raw_client, bootstrap_ajax_cookie=False)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(client.get_html, ["https://vietlott.vn/result"] * 4))
    assert all(result.html == "ok" for result in results)
    assert len(requested_at) == 5
    assert requested_at[1] >= 60
    assert all(
        right - left >= 2 for left, right in zip(requested_at, requested_at[1:], strict=False)
    )


def test_last_rate_limited_attempt_also_pauses_other_requests() -> None:
    clock = [0.0]
    requested_at: list[float] = []

    def sleep(delay: float) -> None:
        clock[0] += delay

    def handler(request: httpx.Request) -> httpx.Response:
        requested_at.append(clock[0])
        return httpx.Response(429 if len(requested_at) == 1 else 200, request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as raw_client,
        patch("vietlott.http.time.monotonic", side_effect=lambda: clock[0]),
        patch("vietlott.http.time.sleep", side_effect=sleep),
    ):
        client = VietlottClient(client=raw_client, retries=0)
        with pytest.raises(TemporaryFetchError, match="Transient HTTP 429"):
            client.get_html("https://vietlott.vn/result")
        client.get_html("https://vietlott.vn/result")
    assert requested_at == [0, 60]


def test_retry_after_supports_http_date_without_shortening_server_delay() -> None:
    assert _retry_after_seconds("120") == 120
    with patch("vietlott.http.datetime") as now:
        now.now.return_value = datetime(2026, 10, 6, 14, tzinfo=UTC)
        assert _retry_after_seconds("Tue, 06 Oct 2026 14:02:00 GMT") == 120


def test_consecutive_rate_limits_from_different_requests_share_backoff() -> None:
    clock = [0.0]
    requested_at: list[float] = []

    def sleep(delay: float) -> None:
        clock[0] += delay

    def handler(request: httpx.Request) -> httpx.Response:
        requested_at.append(clock[0])
        return httpx.Response(429 if len(requested_at) < 4 else 200, request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as raw_client,
        patch("vietlott.http.time.monotonic", side_effect=lambda: clock[0]),
        patch("vietlott.http.time.sleep", side_effect=sleep),
    ):
        client = VietlottClient(client=raw_client, retries=0)
        for _ in range(3):
            with pytest.raises(TemporaryFetchError):
                client.get_html("https://vietlott.vn/result")
        client.get_html("https://vietlott.vn/result")
    assert requested_at == [0, 60, 180, 420]


@pytest.mark.parametrize("value", [None, "invalid", "nan", "inf", "-1"])
def test_invalid_retry_after_uses_fallback(value: str | None) -> None:
    assert _retry_after_seconds(value) is None


def test_access_denied_is_never_parsed() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(403, request=request))
    raw_client = httpx.Client(transport=transport)
    client = VietlottClient(
        request_interval=0, client=raw_client, retries=1, bootstrap_ajax_cookie=False
    )
    with pytest.raises(FetchError):
        client.post_ajax("https://vietlott.vn/ajaxpro/result.ashx", {})
    raw_client.close()


def test_ajax_response_requires_html_content() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, request=request, json={"value": {}})
    )
    raw_client = httpx.Client(transport=transport)
    client = VietlottClient(
        request_interval=0, client=raw_client, retries=1, bootstrap_ajax_cookie=False
    )
    with pytest.raises(ParseError):
        client.post_ajax("https://vietlott.vn/ajaxpro/result.ashx", {})
    raw_client.close()


def test_transient_failure_is_retried_three_times() -> None:
    attempts = 0

    def unavailable(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, request=request)

    raw_client = httpx.Client(transport=httpx.MockTransport(unavailable))
    client = VietlottClient(
        request_interval=0,
        client=raw_client,
        retries=3,
        backoff_base=0,
        bootstrap_ajax_cookie=False,
    )
    with patch("vietlott.http.time.sleep"), pytest.raises(FetchError):
        client.get_html("https://vietlott.vn/result")
    assert attempts == 4
    raw_client.close()


def test_rate_limit_honors_retry_after_and_recovers() -> None:
    attempts = 0

    def rate_limited(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, request=request, headers={"Retry-After": "2"})
        return httpx.Response(200, request=request, text="ok")

    raw_client = httpx.Client(transport=httpx.MockTransport(rate_limited))
    client = VietlottClient(
        request_interval=0,
        client=raw_client,
        retries=1,
        bootstrap_ajax_cookie=False,
    )
    with (
        patch("vietlott.http.time.monotonic", return_value=0),
        patch("vietlott.http.time.sleep") as sleep,
    ):
        response = client.get_html("https://vietlott.vn/result")
    assert response.html == "ok"
    assert attempts == 2
    sleep.assert_called_once_with(2.0)
    raw_client.close()


def test_non_official_url_is_rejected_before_request() -> None:
    raw_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: pytest.fail(f"unexpected request: {request.url}")
        )
    )
    client = VietlottClient(request_interval=0, client=raw_client, bootstrap_ajax_cookie=False)
    with pytest.raises(FetchError):
        client.get_html("https://example.com/result")
    raw_client.close()


def test_ajax_bootstrap_sends_first_party_cookie() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                request=request,
                text='<script>document.cookie="vietlott_edge=abc123; path=/"</script>',
            )
        assert request.headers["cookie"] == "vietlott_edge=abc123"
        return httpx.Response(
            200,
            request=request,
            json={"value": {"HtmlContent": "<table></table>"}},
        )

    raw_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = VietlottClient(request_interval=0, client=raw_client)
    client.post_ajax("https://vietlott.vn/ajaxpro/result.ashx", {})
    assert [request.method for request in requests] == ["GET", "POST"]
    raw_client.close()


def test_relay_forwards_auth_and_preserves_official_source_url() -> None:
    official_url = "https://www.vietlott.vn/ajaxpro/result.ashx"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "relay.example"
        assert request.url.params["url"] == official_url
        assert request.headers["authorization"] == "Bearer relay-secret"
        return httpx.Response(
            200,
            request=request,
            headers={"X-Vietlott-Source-Url": official_url},
            json={"value": {"HtmlContent": "<table></table>"}},
        )

    raw_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = VietlottClient(
        request_interval=0,
        client=raw_client,
        bootstrap_ajax_cookie=False,
        relay_url="https://relay.example",
        relay_token="relay-secret",
    )
    response = client.post_ajax(official_url, {})
    assert response.url == official_url
    raw_client.close()


def test_relay_bootstrap_cookie_is_sent_back_through_relay() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        source_url = request.url.params["url"]
        if request.method == "GET":
            return httpx.Response(
                200,
                request=request,
                headers={"X-Vietlott-Source-Url": source_url},
                text='<script>document.cookie="vietlott_edge=abc123; path=/"</script>',
            )
        assert request.headers["cookie"] == "vietlott_edge=abc123"
        return httpx.Response(
            200,
            request=request,
            headers={"X-Vietlott-Source-Url": source_url},
            json={"value": {"HtmlContent": "<table></table>"}},
        )

    raw_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = VietlottClient(
        request_interval=0,
        client=raw_client,
        relay_url="https://relay.example",
        relay_token="relay-secret",
    )
    client.post_ajax("https://www.vietlott.vn/ajaxpro/result.ashx", {})
    assert [request.method for request in requests] == ["GET", "POST"]
    raw_client.close()
