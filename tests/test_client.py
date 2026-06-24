"""Tests for the FRED httpx client wrapper.

All HTTP is mocked with respx — NO real FRED call is made.  Covers
config resolution, the token bucket, retry/error mapping, key injection,
and SSRF/redaction guarantees.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from fred_macro_mcp import client as client_mod
from fred_macro_mcp.client import (
    DEFAULT_RATE_LIMIT_PER_MIN,
    FRED_HARD_RATE_LIMIT_PER_MIN,
    FRED_HOST,
    FredClient,
    TokenBucket,
    make_client,
    resolve_api_key,
    resolve_rate_limit,
)
from fred_macro_mcp.errors import (
    FredApiError,
    FredConfigurationError,
    FredNotFoundError,
    FredRateLimitError,
    FredTransientError,
)

KEY = "abcdef0123456789abcdef0123456789"  # pragma: allowlist secret - test token
OBS_URL = f"{FRED_HOST}/fred/series/observations"


# ---------------------------------------------------------------------------
# resolve_api_key
# ---------------------------------------------------------------------------


def test_resolve_api_key_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", KEY)
    assert resolve_api_key() == KEY


def test_resolve_api_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(FredConfigurationError) as exc:
        resolve_api_key()
    assert "not set" in exc.value.hint


def test_resolve_api_key_malformed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "TOO-SHORT")
    with pytest.raises(FredConfigurationError) as exc:
        resolve_api_key()
    # The malformed value must NOT appear in the hint.
    assert "TOO-SHORT" not in exc.value.hint


# ---------------------------------------------------------------------------
# resolve_rate_limit
# ---------------------------------------------------------------------------


def test_rate_limit_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRED_RATE_LIMIT_PER_MIN", raising=False)
    assert resolve_rate_limit() == DEFAULT_RATE_LIMIT_PER_MIN


def test_rate_limit_custom(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_RATE_LIMIT_PER_MIN", "30")
    assert resolve_rate_limit() == 30


def test_rate_limit_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_RATE_LIMIT_PER_MIN", "500")
    assert resolve_rate_limit() == FRED_HARD_RATE_LIMIT_PER_MIN


def test_rate_limit_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_RATE_LIMIT_PER_MIN", "0")
    assert resolve_rate_limit() == 1


def test_rate_limit_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_RATE_LIMIT_PER_MIN", "abc")
    assert resolve_rate_limit() == DEFAULT_RATE_LIMIT_PER_MIN


def test_rate_limit_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_RATE_LIMIT_PER_MIN", "")
    assert resolve_rate_limit() == DEFAULT_RATE_LIMIT_PER_MIN


# ---------------------------------------------------------------------------
# TokenBucket
# ---------------------------------------------------------------------------


def test_token_bucket_invalid_capacity() -> None:
    with pytest.raises(ValueError):
        TokenBucket(0)


async def test_token_bucket_admits_within_capacity() -> None:
    bucket = TokenBucket(3)
    for _ in range(3):
        await bucket.acquire()
    assert bucket.tokens_remaining() == 0


async def test_token_bucket_blocks_when_full(monkeypatch: pytest.MonkeyPatch) -> None:
    bucket = TokenBucket(1)
    await bucket.acquire()

    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(secs: float) -> None:
        slept.append(secs)
        # Force the window to roll over so the next loop admits.
        bucket._timestamps.clear()
        await real_sleep(0)

    monkeypatch.setattr(client_mod.asyncio, "sleep", fake_sleep)
    await bucket.acquire()
    assert slept and slept[0] > 0


def test_token_bucket_tokens_remaining_evicts_old() -> None:
    bucket = TokenBucket(2)
    bucket._timestamps.append(0.0)  # ancient timestamp, outside window
    assert bucket.tokens_remaining() == 2


async def test_token_bucket_acquire_evicts_old_timestamp() -> None:
    # An ancient timestamp (monotonic 0.0) is well outside the 60s window;
    # acquire() must evict it (client.py eviction loop) and admit.
    bucket = TokenBucket(1)
    bucket._timestamps.append(0.0)
    await bucket.acquire()  # evicts the stale entry, then admits
    assert bucket.tokens_remaining() == 0


# ---------------------------------------------------------------------------
# Client helpers
# ---------------------------------------------------------------------------


def _client() -> FredClient:
    return FredClient(api_key=KEY, rate_limit_per_min=120)


def test_merged_params_injects_key_and_filetype() -> None:
    c = _client()
    merged = c._merged_params({"series_id": "GDP", "skip": None})
    assert merged["api_key"] == KEY
    assert merged["file_type"] == "json"
    assert merged["series_id"] == "GDP"
    assert "skip" not in merged  # None values dropped


def test_merged_params_none() -> None:
    c = _client()
    merged = c._merged_params(None)
    assert merged == {"api_key": KEY, "file_type": "json"}


# ---------------------------------------------------------------------------
# Request paths (respx)
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_json_ok() -> None:
    respx.get(OBS_URL).mock(return_value=httpx.Response(200, json={"observations": []}))
    async with _client() as c:
        data = await c.get_json("/fred/series/observations", params={"series_id": "GDP"})
    assert data == {"observations": []}


@respx.mock
async def test_get_json_sends_key_as_param() -> None:
    route = respx.get(OBS_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
    async with _client() as c:
        await c.get_json("/fred/series/observations", params={"series_id": "GDP"})
    request = route.calls.last.request
    assert f"api_key={KEY}" in str(request.url)
    assert "file_type=json" in str(request.url)


@respx.mock
async def test_get_json_404() -> None:
    respx.get(OBS_URL).mock(return_value=httpx.Response(404))
    async with _client() as c:
        with pytest.raises(FredNotFoundError):
            await c.get_json("/fred/series/observations")


@respx.mock
async def test_get_json_400_api_error() -> None:
    respx.get(OBS_URL).mock(
        return_value=httpx.Response(400, json={"error_code": 400, "error_message": "Bad Request. series_id"})
    )
    async with _client() as c:
        with pytest.raises(FredApiError) as exc:
            await c.get_json("/fred/series/observations")
    assert exc.value.status_code == 400
    assert "series_id" in exc.value.hint


@respx.mock
async def test_get_json_403_bad_key_redacted() -> None:
    respx.get(OBS_URL).mock(return_value=httpx.Response(403, json={"error_code": 403, "error_message": "Bad key"}))
    async with _client() as c:
        with pytest.raises(FredApiError) as exc:
            await c.get_json("/fred/series/observations")
    assert KEY not in str(exc.value)


@respx.mock
async def test_get_json_4xx_no_body() -> None:
    respx.get(OBS_URL).mock(return_value=httpx.Response(400, content=b"not json"))
    async with _client() as c:
        with pytest.raises(FredApiError) as exc:
            await c.get_json("/fred/series/observations")
    assert "400" in exc.value.hint


@respx.mock
async def test_get_json_4xx_body_without_message() -> None:
    respx.get(OBS_URL).mock(return_value=httpx.Response(400, json={"other": "x"}))
    async with _client() as c:
        with pytest.raises(FredApiError):
            await c.get_json("/fred/series/observations")


@respx.mock
async def test_get_json_4xx_list_body() -> None:
    respx.get(OBS_URL).mock(return_value=httpx.Response(400, json=[1, 2, 3]))
    async with _client() as c:
        with pytest.raises(FredApiError):
            await c.get_json("/fred/series/observations")


@respx.mock
async def test_get_json_429_retries_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod.asyncio, "sleep", _no_sleep)
    respx.get(OBS_URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "1"}))
    async with _client() as c:
        with pytest.raises(FredRateLimitError) as exc:
            await c.get_json("/fred/series/observations")
    assert exc.value.retry_after_seconds == 1


@respx.mock
async def test_get_json_429_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod.asyncio, "sleep", _no_sleep)
    respx.get(OBS_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(200, json={"ok": 1}),
        ]
    )
    async with _client() as c:
        data = await c.get_json("/fred/series/observations")
    assert data == {"ok": 1}


@respx.mock
async def test_get_json_5xx_retries_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod.asyncio, "sleep", _no_sleep)
    respx.get(OBS_URL).mock(return_value=httpx.Response(503))
    async with _client() as c:
        with pytest.raises(FredTransientError) as exc:
            await c.get_json("/fred/series/observations")
    assert exc.value.status_code == 503


@respx.mock
async def test_get_json_5xx_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod.asyncio, "sleep", _no_sleep)
    respx.get(OBS_URL).mock(side_effect=[httpx.Response(500), httpx.Response(200, json={"ok": 1})])
    async with _client() as c:
        data = await c.get_json("/fred/series/observations")
    assert data == {"ok": 1}


@respx.mock
async def test_get_json_network_error_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod.asyncio, "sleep", _no_sleep)
    respx.get(OBS_URL).mock(side_effect=httpx.ConnectError("boom"))
    async with _client() as c:
        with pytest.raises(FredTransientError) as exc:
            await c.get_json("/fred/series/observations")
    assert exc.value.status_code == 0


@respx.mock
async def test_get_json_network_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod.asyncio, "sleep", _no_sleep)
    respx.get(OBS_URL).mock(side_effect=[httpx.ReadTimeout("slow"), httpx.Response(200, json={"ok": 1})])
    async with _client() as c:
        data = await c.get_json("/fred/series/observations")
    assert data == {"ok": 1}


@respx.mock
async def test_get_json_invalid_json() -> None:
    respx.get(OBS_URL).mock(return_value=httpx.Response(200, content=b"<<not json>>"))
    async with _client() as c:
        with pytest.raises(FredTransientError) as exc:
            await c.get_json("/fred/series/observations")
    assert "invalid json" in exc.value.hint


@respx.mock
async def test_get_json_unexpected_shape() -> None:
    respx.get(OBS_URL).mock(return_value=httpx.Response(200, json=[1, 2, 3]))
    async with _client() as c:
        with pytest.raises(FredTransientError) as exc:
            await c.get_json("/fred/series/observations")
    assert "unexpected json shape" in exc.value.hint


# ---------------------------------------------------------------------------
# Retry-After parsing
# ---------------------------------------------------------------------------


@respx.mock
async def test_retry_after_non_numeric(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod.asyncio, "sleep", _no_sleep)
    respx.get(OBS_URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "soon"}))
    async with _client() as c:
        with pytest.raises(FredRateLimitError) as exc:
            await c.get_json("/fred/series/observations")
    assert exc.value.retry_after_seconds == 1


@respx.mock
async def test_retry_after_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod.asyncio, "sleep", _no_sleep)
    respx.get(OBS_URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "999"}))
    async with _client() as c:
        with pytest.raises(FredRateLimitError) as exc:
            await c.get_json("/fred/series/observations")
    assert exc.value.retry_after_seconds == 60


# ---------------------------------------------------------------------------
# make_client
# ---------------------------------------------------------------------------


def test_make_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", KEY)
    c = make_client()
    assert isinstance(c, FredClient)


def test_backoff_delay_positive() -> None:
    assert client_mod._backoff_delay(0) > 0
    assert client_mod._backoff_delay(3) >= client_mod._backoff_delay(0)


async def _no_sleep(_secs: float) -> None:
    return None
