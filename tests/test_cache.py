"""Tests for the Cache facade (per-table API + stats + singleton)."""

from __future__ import annotations

import pytest

import fred_macro_mcp.cache as cache_mod
from fred_macro_mcp.cache import (
    Cache,
    CacheStats,
    cache_bypass,
    cache_enabled,
    get_cache,
    reset_cache_singleton,
)
from fred_macro_mcp.cache_backend import MemoryBackend


@pytest.fixture
def cache() -> Cache:
    return Cache(backend=MemoryBackend())


# ---------------------------------------------------------------------------
# Toggles
# ---------------------------------------------------------------------------


def test_cache_enabled_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRED_CACHE_ENABLED", raising=False)
    assert cache_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "YES", "on"])
def test_cache_enabled_truthy(monkeypatch: pytest.MonkeyPatch, val: str) -> None:
    monkeypatch.setenv("FRED_CACHE_ENABLED", val)
    assert cache_enabled() is True


def test_cache_enabled_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_CACHE_ENABLED", "")
    assert cache_enabled() is False


def test_cache_bypass_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRED_CACHE_BYPASS", raising=False)
    assert cache_bypass() is False


def test_cache_bypass_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_CACHE_BYPASS", "1")
    assert cache_bypass() is True


# ---------------------------------------------------------------------------
# Per-table round-trips
# ---------------------------------------------------------------------------


def test_series_obs_roundtrip(cache: Cache) -> None:
    params = {"series_id": "GDP", "limit": 10}
    assert cache.get_series_obs(params) is None
    cache.put_series_obs(params, {"observations": [1, 2]})
    assert cache.get_series_obs(params) == {"observations": [1, 2]}


def test_series_search_roundtrip(cache: Cache) -> None:
    params = {"query": "gdp"}
    cache.put_series_search(params, {"results": []})
    assert cache.get_series_search(params) == {"results": []}


def test_series_meta_roundtrip(cache: Cache) -> None:
    params = {"series_id": "GDP"}
    cache.put_series_meta(params, {"title": "GDP"})
    assert cache.get_series_meta(params) == {"title": "GDP"}


def test_releases_roundtrip(cache: Cache) -> None:
    params = {"days": 14, "as_of": "2026-06-25"}
    cache.put_releases(params, {"releases": []})
    assert cache.get_releases(params) == {"releases": []}


def test_distinct_params_distinct_keys(cache: Cache) -> None:
    cache.put_series_obs({"series_id": "GDP"}, {"v": 1})
    cache.put_series_obs({"series_id": "CPI"}, {"v": 2})
    assert cache.get_series_obs({"series_id": "GDP"}) == {"v": 1}
    assert cache.get_series_obs({"series_id": "CPI"}) == {"v": 2}


# ---------------------------------------------------------------------------
# Stats + lifecycle
# ---------------------------------------------------------------------------


def test_stats(monkeypatch: pytest.MonkeyPatch, cache: Cache) -> None:
    monkeypatch.setenv("FRED_CACHE_ENABLED", "1")
    cache.put_series_obs({"series_id": "GDP"}, {"v": 1})
    stats = cache.get_stats()
    assert isinstance(stats, CacheStats)
    assert stats.backend == "memory"
    assert stats.entries == 1
    assert stats.to_dict()["backend"] == "memory"


def test_stats_size_failure() -> None:
    class BoomBackend(MemoryBackend):
        def size(self) -> int:
            raise RuntimeError("boom")

    c = Cache(backend=BoomBackend())
    assert c.get_stats().entries == 0


def test_reset(cache: Cache) -> None:
    cache.put_series_obs({"series_id": "GDP"}, {"v": 1})
    cache.reset()
    assert cache.get_series_obs({"series_id": "GDP"}) is None


def test_context_manager_closes() -> None:
    with Cache(backend=MemoryBackend()) as c:
        assert c.close() is None


def test_default_backend_is_memory() -> None:
    assert Cache().backend.name == "memory"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def test_get_cache_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_CACHE_ENABLED", "0")
    reset_cache_singleton()
    assert get_cache() is None


def test_get_cache_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_CACHE_ENABLED", "1")
    reset_cache_singleton()
    first = get_cache()
    second = get_cache()
    assert first is second
    assert first is not None


def test_reset_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_CACHE_ENABLED", "1")
    reset_cache_singleton()
    first = get_cache()
    reset_cache_singleton()
    second = get_cache()
    assert first is not second


def test_reset_singleton_when_none() -> None:
    reset_cache_singleton()
    # Calling again with no live singleton is a no-op (covers the guard).
    reset_cache_singleton()
    assert cache_mod._singleton is None
