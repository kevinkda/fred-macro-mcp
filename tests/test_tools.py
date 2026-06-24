"""Tests for the business + meta tool implementations and the runtime helper.

All upstream HTTP is mocked with respx — no real FRED call is made.
"""

from __future__ import annotations

import datetime as _dt

import httpx
import pytest
import respx

import fred_macro_mcp.tools._runtime as runtime_mod
import fred_macro_mcp.tools.releases as releases_mod
from fred_macro_mcp.client import FRED_HOST, FredClient
from fred_macro_mcp.models import (
    GetReleaseCalendarInput,
    GetSeriesInput,
    GetSeriesLatestInput,
    SearchSeriesInput,
)
from fred_macro_mcp.tools import meta, releases, search, series
from fred_macro_mcp.tools._runtime import call_with_cache, get_client, set_client_for_tests

KEY = "abcdef0123456789abcdef0123456789"  # pragma: allowlist secret  # gitleaks:allow
OBS_URL = f"{FRED_HOST}/fred/series/observations"
SEARCH_URL = f"{FRED_HOST}/fred/series/search"
RELEASES_URL = f"{FRED_HOST}/fred/releases/dates"


@pytest.fixture(autouse=True)
async def _inject_client() -> None:
    """Use a real FredClient (respx intercepts the transport)."""
    await set_client_for_tests(FredClient(api_key=KEY, rate_limit_per_min=120))


# ---------------------------------------------------------------------------
# get_series
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_series_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_CACHE_ENABLED", "0")  # exercise disabled path
    respx.get(OBS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "units": "lin",
                "observations": [
                    {"date": "2024-01-01", "value": "100.5"},
                    {"date": "2024-02-01", "value": "."},  # missing
                    {"date": "2024-03-01", "value": "bad"},  # unparseable
                    "not-a-dict",
                    {"value": "no-date"},
                ],
            },
        )
    )
    out = await series.get_series_impl(GetSeriesInput(series_id="GDP"))
    assert out["series_id"] == "GDP"
    assert out["observation_count"] == 3
    assert out["observations"][0] == {"date": "2024-01-01", "value": 100.5}
    assert out["observations"][1]["value"] is None  # "."
    assert out["observations"][2]["value"] is None  # "bad"
    assert out["_cache_status"] == "disabled"


@respx.mock
async def test_get_series_no_observations_key() -> None:
    respx.get(OBS_URL).mock(return_value=httpx.Response(200, json={"units": "lin"}))
    out = await series.get_series_impl(GetSeriesInput(series_id="GDP"))
    assert out["observation_count"] == 0


@respx.mock
async def test_get_series_observations_not_list() -> None:
    respx.get(OBS_URL).mock(return_value=httpx.Response(200, json={"observations": "nope"}))
    out = await series.get_series_impl(GetSeriesInput(series_id="GDP"))
    assert out["observations"] == []


@respx.mock
async def test_get_series_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_CACHE_ENABLED", "1")
    route = respx.get(OBS_URL).mock(
        return_value=httpx.Response(200, json={"observations": [{"date": "2024-01-01", "value": "1"}]})
    )
    args = GetSeriesInput(series_id="GDP", start="2024-01-01")
    first = await series.get_series_impl(args)
    assert first["_cache_status"] == "miss"
    second = await series.get_series_impl(args)
    assert second["_cache_status"] == "hit"
    assert route.call_count == 1  # second served from cache


# ---------------------------------------------------------------------------
# get_series_latest
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_series_latest_ok() -> None:
    respx.get(OBS_URL).mock(
        return_value=httpx.Response(
            200, json={"units": "pct", "observations": [{"date": "2024-05-01", "value": "3.9"}]}
        )
    )
    out = await series.get_series_latest_impl(GetSeriesLatestInput(series_id="UNRATE"))
    assert out["latest"] == {"date": "2024-05-01", "value": 3.9}
    assert out["units"] == "pct"


@respx.mock
async def test_get_series_latest_empty() -> None:
    respx.get(OBS_URL).mock(return_value=httpx.Response(200, json={"observations": []}))
    out = await series.get_series_latest_impl(GetSeriesLatestInput(series_id="UNRATE"))
    assert out["latest"] is None


# ---------------------------------------------------------------------------
# search_series
# ---------------------------------------------------------------------------


@respx.mock
async def test_search_ok() -> None:
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "seriess": [
                    {
                        "id": "GDPC1",
                        "title": "Real GDP",
                        "frequency": "Quarterly",
                        "units": "Bil. Chn. 2017$",
                        "seasonal_adjustment": "SAAR",
                        "observation_start": "1947-01-01",
                        "observation_end": "2024-01-01",
                        "popularity": 90,
                    },
                    "not-a-dict",
                ]
            },
        )
    )
    out = await search.search_series_impl(SearchSeriesInput(query="real gdp"))
    assert out["result_count"] == 1
    assert out["results"][0]["id"] == "GDPC1"


@respx.mock
async def test_search_no_seriess_key() -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={}))
    out = await search.search_series_impl(SearchSeriesInput(query="x"))
    assert out["results"] == []


@respx.mock
async def test_search_seriess_not_list() -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"seriess": "no"}))
    out = await search.search_series_impl(SearchSeriesInput(query="x"))
    assert out["results"] == []


# ---------------------------------------------------------------------------
# get_release_calendar
# ---------------------------------------------------------------------------


@respx.mock
async def test_release_calendar_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(releases_mod, "_today", lambda: _dt.date(2026, 6, 25))
    respx.get(RELEASES_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "release_dates": [
                    {"release_id": 10, "release_name": "CPI", "date": "2026-06-26"},
                    {"release_id": 11, "release_name": "GDP", "date": "2026-07-30"},  # outside 14d
                    {"release_id": 12, "release_name": "Past", "date": "2026-06-01"},  # before today
                    {"release_id": 13, "release_name": "Bad", "date": "not-a-date"},
                    "not-a-dict",
                    {"release_id": 14, "release_name": "NoDate"},  # missing date
                ]
            },
        )
    )
    out = await releases.get_release_calendar_impl(GetReleaseCalendarInput(days=14))
    assert out["from_date"] == "2026-06-25"
    assert out["release_count"] == 1
    assert out["releases"][0]["release_name"] == "CPI"


@respx.mock
async def test_release_calendar_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(releases_mod, "_today", lambda: _dt.date(2026, 6, 25))
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json={}))
    out = await releases.get_release_calendar_impl(GetReleaseCalendarInput(days=7))
    assert out["releases"] == []


@respx.mock
async def test_release_calendar_dates_not_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(releases_mod, "_today", lambda: _dt.date(2026, 6, 25))
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json={"release_dates": "no"}))
    out = await releases.get_release_calendar_impl(GetReleaseCalendarInput(days=7))
    assert out["releases"] == []


def test_releases_today_is_a_date() -> None:
    # Exercise the real clock helper.
    assert isinstance(releases_mod._today(), _dt.date)


# ---------------------------------------------------------------------------
# _runtime
# ---------------------------------------------------------------------------


async def test_get_client_lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    await set_client_for_tests(None)
    monkeypatch.setenv("FRED_API_KEY", KEY)
    runtime_mod.reset_client_cache()
    c1 = await get_client()
    c2 = await get_client()
    assert c1 is c2


async def test_call_with_cache_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_CACHE_ENABLED", "1")
    monkeypatch.setenv("FRED_CACHE_BYPASS", "1")

    async def fetch(_c: FredClient) -> dict:
        return {"v": 1}

    def lookup(_cache: object) -> dict | None:
        raise AssertionError("lookup must be skipped on bypass")

    stored: list[dict] = []

    def store(_cache: object, raw: dict) -> None:
        stored.append(raw)

    out = await call_with_cache(fetch, cache_lookup=lookup, cache_store=store)
    assert out["_cache_status"] == "bypass"
    assert not stored  # store skipped on bypass


async def test_call_with_cache_lookup_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_CACHE_ENABLED", "1")

    async def fetch(_c: FredClient) -> dict:
        return {"v": 1}

    def lookup(_cache: object) -> dict | None:
        raise RuntimeError("boom")  # swallowed -> miss

    out = await call_with_cache(fetch, cache_lookup=lookup)
    assert out["_cache_status"] == "miss"


async def test_call_with_cache_store_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_CACHE_ENABLED", "1")

    async def fetch(_c: FredClient) -> dict:
        return {"v": 1}

    def store(_cache: object, _raw: dict) -> None:
        raise RuntimeError("store boom")  # swallowed

    out = await call_with_cache(fetch, cache_store=store)
    assert out["_cache_status"] == "miss"


async def test_call_with_cache_no_hooks_disabled() -> None:
    async def fetch(_c: FredClient) -> dict:
        return {"v": 1}

    out = await call_with_cache(fetch)
    assert out["_cache_status"] == "disabled"


async def test_call_with_cache_lookup_non_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_CACHE_ENABLED", "1")

    async def fetch(_c: FredClient) -> dict:
        return {"v": 1}

    def lookup(_cache: object) -> dict | None:
        return None  # miss path

    out = await call_with_cache(fetch, cache_lookup=lookup)
    assert out["_cache_status"] == "miss"


# ---------------------------------------------------------------------------
# meta tools
# ---------------------------------------------------------------------------


async def test_health_check_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", KEY)
    monkeypatch.setenv("FRED_CACHE_ENABLED", "0")
    out = await meta.health_check_impl()
    assert out["overall_status"] == "ok"
    assert out["api_key_configured"] is True
    assert out["rate_limit_per_min"] == 120
    assert out["cache_enabled"] is False


async def test_health_check_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    out = await meta.health_check_impl()
    assert out["overall_status"] == "unhealthy"
    assert out["api_key_configured"] is False
    assert out["api_key_reason"] == "missing"  # pragma: allowlist secret - assertion, not a secret
    assert out["rate_limit_per_min"] is None


async def test_health_check_malformed_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "SHORT")
    out = await meta.health_check_impl()
    assert out["api_key_configured"] is False
    assert "SHORT" not in str(out["api_key_reason"])


async def test_health_check_cache_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", KEY)
    monkeypatch.setenv("FRED_CACHE_ENABLED", "1")
    out = await meta.health_check_impl()
    assert out["cache_enabled"] is True
    assert out["cache_backend"] == "memory"


async def test_health_check_cache_summary_get_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", KEY)
    monkeypatch.setenv("FRED_CACHE_ENABLED", "1")
    monkeypatch.setattr(meta, "get_cache", lambda: None)
    out = await meta.health_check_impl()
    assert out["cache_enabled"] is False


async def test_health_check_cache_stats_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", KEY)
    monkeypatch.setenv("FRED_CACHE_ENABLED", "1")

    class Boom:
        def get_stats(self) -> object:
            raise RuntimeError("boom")

    monkeypatch.setattr(meta, "get_cache", lambda: Boom())
    out = await meta.health_check_impl()
    assert out["cache_enabled"] is True
    assert out["cache_backend"] is None


async def test_server_info(monkeypatch: pytest.MonkeyPatch) -> None:
    out = await meta.get_server_info_impl(server_version="0.1.0")
    assert out["server_version"] == "0.1.0"
    assert "get_series" in out["supported_tools"]
    assert "." in out["python_version"]
