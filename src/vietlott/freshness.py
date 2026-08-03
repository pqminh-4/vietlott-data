"""Independent SLA checks for canonical data and the published static API."""

from __future__ import annotations

import time as time_module
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx

from vietlott.config import GameSpec, get_game
from vietlott.errors import FreshnessError, ValidationError
from vietlott.models import DrawRecord
from vietlott.schedule import TIMEZONE, local_now
from vietlott.storage import DataStore

STATUS_PRIORITY = {"fresh": 0, "pending": 1, "stale": 2, "invalid": 3}
SLOT_HOURS = {"afternoon": 13, "evening": 18, "night": 21}


def check_freshness(
    store: DataStore,
    games: Iterable[str],
    *,
    max_delay_minutes: int = 60,
    as_of: datetime | None = None,
    api_base_url: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Return a stable JSON-ready freshness report for the selected games."""
    if max_delay_minutes < 1:
        raise FreshnessError("max_delay_minutes must be positive")
    current = as_of or local_now()
    if current.tzinfo is None:
        raise FreshnessError("as_of must include a UTC offset")
    current = current.astimezone(TIMEZONE)
    base_url = _validate_api_base_url(api_base_url) if api_base_url else None
    owns_client = base_url is not None and client is None
    remote_client = client or (
        httpx.Client(timeout=20.0, follow_redirects=True) if base_url else None
    )
    results: list[dict[str, Any]] = []
    try:
        for game in games:
            spec = get_game(game)
            scheduled_at = _latest_scheduled_draw(spec, current)
            deadline = scheduled_at + timedelta(minutes=max_delay_minutes)
            expected: dict[str, Any] = {
                "draw_id": None,
                "draw_date": scheduled_at.date().isoformat(),
                "draw_time": scheduled_at.time().isoformat(),
                "draw_slot": _slot_for_hour(scheduled_at.hour),
                "scheduled_at": scheduled_at.isoformat(),
                "deadline": deadline.isoformat(),
            }
            result: dict[str, Any]
            actual: DrawRecord | None = None
            try:
                actual = _load_latest(store, game, base_url, remote_client)
                actual_at = _record_schedule_time(actual, spec)
                expected["draw_id"] = _expected_draw_id(actual, actual_at, scheduled_at, spec)
                if actual_at > current:
                    raise FreshnessError("latest record is dated in the future")
                if actual_at >= scheduled_at:
                    status = "fresh"
                elif current <= deadline:
                    status = "pending"
                else:
                    status = "stale"
                result = {
                    "game": game,
                    "expected": expected,
                    "actual": _actual_payload(actual, actual_at),
                    "status": status,
                }
            except (
                FreshnessError,
                ValidationError,
                httpx.HTTPError,
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                result = {
                    "game": game,
                    "expected": expected,
                    "actual": _raw_actual_payload(actual) if actual is not None else None,
                    "status": "invalid",
                    "error": str(exc),
                }
            results.append(result)
    finally:
        if owns_client and remote_client is not None:
            remote_client.close()

    overall = max(
        (str(result["status"]) for result in results),
        key=lambda status: STATUS_PRIORITY[status],
        default="fresh",
    )
    return {
        "as_of": current.isoformat(),
        "timezone": str(TIMEZONE),
        "max_delay_minutes": max_delay_minutes,
        "source": {
            "kind": "api" if base_url else "canonical",
            "value": base_url or str(store.root),
        },
        "overall_status": overall,
        "games": results,
    }


def _validate_api_base_url(value: str) -> str:
    normalized = value.rstrip("/")
    parsed = urlparse(normalized)
    try:
        port = parsed.port
    except ValueError as exc:
        raise FreshnessError("api_base_url contains an invalid port") from exc
    is_local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    if (parsed.scheme != "https" and not is_local_http) or not parsed.hostname:
        raise FreshnessError("api_base_url must be an HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise FreshnessError("api_base_url must be a plain URL without credentials or query data")
    if port not in {None, 443} and not is_local_http:
        raise FreshnessError("HTTPS api_base_url must use the default port")
    return normalized


def _latest_scheduled_draw(spec: GameSpec, current: datetime) -> datetime:
    for days_ago in range(15):
        candidate_date = current.date() - timedelta(days=days_ago)
        if candidate_date.weekday() not in spec.weekdays:
            continue
        candidates = [
            datetime.combine(candidate_date, time(hour=hour), tzinfo=TIMEZONE)
            for hour in spec.draw_hours
        ]
        eligible = [candidate for candidate in candidates if candidate <= current]
        if eligible:
            return max(eligible)
    raise FreshnessError(f"No scheduled draw found for {spec.code}")


def _load_latest(
    store: DataStore,
    game: str,
    base_url: str | None,
    client: httpx.Client | None,
) -> DrawRecord:
    if base_url is None:
        records = store.load(game)
        if not records:
            raise FreshnessError("canonical dataset has no records")
        return max(records, key=lambda record: int(record.draw_id))
    assert client is not None
    response = _get_with_retry(client, f"{base_url}/{game}/latest.json")
    payload = response.json()
    if not isinstance(payload, dict):
        raise FreshnessError("latest API response must be a JSON object")
    return DrawRecord.from_dict(payload)


def _get_with_retry(client: httpx.Client, url: str) -> httpx.Response:
    retryable = {404, 408, 425, 429, 500, 502, 503, 504}
    for attempt in range(3):
        try:
            response = client.get(url)
        except httpx.RequestError:
            if attempt == 2:
                raise
        else:
            if response.is_success:
                return response
            if response.status_code not in retryable or attempt == 2:
                response.raise_for_status()
        time_module.sleep(2**attempt)
    raise FreshnessError("API request failed after retries")  # pragma: no cover


def _record_schedule_time(record: DrawRecord, spec: GameSpec) -> datetime:
    record_date = date.fromisoformat(record.draw_date)
    if record_date.weekday() not in spec.weekdays:
        raise FreshnessError("latest record is outside the configured draw weekdays")
    if record.draw_time is not None:
        parsed_time = time.fromisoformat(record.draw_time)
        if parsed_time.minute or parsed_time.second or parsed_time.microsecond:
            raise FreshnessError("latest record has an off-schedule draw_time")
        hour = parsed_time.hour
    elif record.draw_slot in SLOT_HOURS:
        hour = SLOT_HOURS[record.draw_slot]
    elif len(spec.draw_hours) == 1:
        hour = spec.draw_hours[0]
    else:
        raise FreshnessError("latest record does not identify its draw slot")
    if hour not in spec.draw_hours:
        raise FreshnessError("latest record has a draw_time outside the configured schedule")
    expected_slot = _slot_for_hour(hour)
    if record.draw_slot is not None and record.draw_slot != expected_slot:
        raise FreshnessError("latest record draw_time and draw_slot disagree")
    return datetime.combine(record_date, time(hour=hour), tzinfo=TIMEZONE)


def _expected_draw_id(
    actual: DrawRecord,
    actual_at: datetime,
    scheduled_at: datetime,
    spec: GameSpec,
) -> str:
    missing = sum(
        1
        for candidate in _scheduled_draws(spec, actual_at.date(), scheduled_at.date())
        if actual_at < candidate <= scheduled_at
    )
    return str(int(actual.draw_id) + missing).zfill(len(actual.draw_id))


def _scheduled_draws(spec: GameSpec, start: date, end: date) -> Iterable[datetime]:
    current = start
    while current <= end:
        if current.weekday() in spec.weekdays:
            for hour in spec.draw_hours:
                yield datetime.combine(current, time(hour=hour), tzinfo=TIMEZONE)
        current += timedelta(days=1)


def _actual_payload(record: DrawRecord, actual_at: datetime) -> dict[str, Any]:
    payload = _raw_actual_payload(record)
    payload["scheduled_at"] = actual_at.isoformat()
    return payload


def _raw_actual_payload(record: DrawRecord) -> dict[str, Any]:
    return {
        "draw_id": record.draw_id,
        "draw_date": record.draw_date,
        "draw_time": record.draw_time,
        "draw_slot": record.draw_slot,
        "retrieved_at": record.retrieved_at,
    }


def _slot_for_hour(hour: int) -> str:
    return {13: "afternoon", 18: "evening", 21: "night"}.get(hour, f"hour-{hour:02d}")
