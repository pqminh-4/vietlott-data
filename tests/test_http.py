from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from vietlott.errors import FetchError, ParseError
from vietlott.http import VietlottClient


def test_access_denied_is_never_parsed() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(403, request=request))
    raw_client = httpx.Client(transport=transport)
    client = VietlottClient(client=raw_client, retries=1, bootstrap_ajax_cookie=False)
    with pytest.raises(FetchError):
        client.post_ajax("https://vietlott.vn/ajaxpro/result.ashx", {})
    raw_client.close()


def test_ajax_response_requires_html_content() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, request=request, json={"value": {}})
    )
    raw_client = httpx.Client(transport=transport)
    client = VietlottClient(client=raw_client, retries=1, bootstrap_ajax_cookie=False)
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
        client=raw_client,
        retries=3,
        backoff_base=0,
        bootstrap_ajax_cookie=False,
    )
    with patch("vietlott.http.time.sleep"), pytest.raises(FetchError):
        client.get_html("https://vietlott.vn/result")
    assert attempts == 4
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
    client = VietlottClient(client=raw_client)
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
        client=raw_client,
        relay_url="https://relay.example",
        relay_token="relay-secret",
    )
    client.post_ajax("https://www.vietlott.vn/ajaxpro/result.ashx", {})
    assert [request.method for request in requests] == ["GET", "POST"]
    raw_client.close()
