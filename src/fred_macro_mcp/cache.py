"""Pluggable response cache for FRED macro data (v0.7 T0).

The cache delegates to a pluggable
:class:`~fred_macro_mcp.cache_backend.CacheBackend`:

* **memory** (default) — in-process LRU + TTL, zero external dependency,
  concurrency-safe, non-blocking (no global ``RLock``, no file locks).
* **clickhouse** (opt-in) — ``pip install fred-macro-mcp[clickhouse]`` +
  ``FRED_CLICKHOUSE_URL`` + ``FRED_CACHE_BACKEND=clickhouse`` for
  derived-analysis history persistence.

Selection via ``FRED_CACHE_BACKEND`` (``memory`` | ``clickhouse``, default
``memory``).

TTLs (FRED data revises on a release cadence, not intraday):
    * series_obs_cache   — 6  h
    * series_search_cache — 24 h
    * series_meta_cache  — 24 h
    * releases_cache     — 6  h

Failure mode: best-effort — every backend swallows storage errors and the
caller falls through to the live API.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from typing import Any, Final

from .cache_backend import (
    CacheBackend,
    get_cache_backend,
)

__all__ = [
    "DEFAULT_TTL_OBS_S",
    "DEFAULT_TTL_RELEASES_S",
    "DEFAULT_TTL_SEARCH_S",
    "DEFAULT_TTL_SERIES_META_S",
    "ENV_CACHE_BYPASS",
    "ENV_CACHE_ENABLED",
    "Cache",
    "CacheStats",
    "cache_bypass",
    "cache_enabled",
    "get_cache",
    "reset_cache_singleton",
]

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_TTL_OBS_S: Final[int] = 6 * 3600
DEFAULT_TTL_SEARCH_S: Final[int] = 24 * 3600
DEFAULT_TTL_SERIES_META_S: Final[int] = 24 * 3600
DEFAULT_TTL_RELEASES_S: Final[int] = 6 * 3600

ENV_CACHE_ENABLED: Final[str] = "FRED_CACHE_ENABLED"
ENV_CACHE_BYPASS: Final[str] = "FRED_CACHE_BYPASS"

_OBS_TABLE: Final[str] = "series_obs_cache"
_SEARCH_TABLE: Final[str] = "series_search_cache"
_SERIES_META_TABLE: Final[str] = "series_meta_cache"
_RELEASES_TABLE: Final[str] = "releases_cache"


def _truthy(raw: str | None, *, default: bool) -> bool:
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def cache_enabled() -> bool:
    """Honor ``FRED_CACHE_ENABLED`` (default off — opt-in)."""
    return _truthy(os.environ.get(ENV_CACHE_ENABLED), default=False)


def cache_bypass() -> bool:
    """Honor ``FRED_CACHE_BYPASS`` (default off — single-call force fresh)."""
    return _truthy(os.environ.get(ENV_CACHE_BYPASS), default=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_params(params: dict[str, Any]) -> str:
    blob = json.dumps(params, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Stats payload
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CacheStats:
    backend: str
    enabled: bool
    entries: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "enabled": self.enabled,
            "entries": self.entries,
        }


# ---------------------------------------------------------------------------
# Cache facade
# ---------------------------------------------------------------------------


class Cache:
    """Backend-agnostic response cache.  One instance per process.

    Delegates all storage to a :class:`CacheBackend` (memory by default,
    ClickHouse when opted in).  Tools key on the request params hash.
    """

    def __init__(self, backend: CacheBackend | None = None) -> None:
        self.backend: CacheBackend = backend if backend is not None else get_cache_backend()

    def close(self) -> None:
        # Pluggable backends own their own lifecycle; nothing to close for
        # the memory backend, and the ClickHouse client is process-scoped.
        return None

    def __enter__(self) -> Cache:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        del exc_type, exc, tb
        self.close()

    # ----------------------------------------------- generic JSON tables

    def _get(self, table: str, key: str) -> dict[str, Any] | None:
        return self.backend.get(table, key)

    def _put(self, table: str, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        self.backend.set(table, key, value, ttl_seconds)

    # ---------------------------------------------- per-table public APIs

    def get_series_obs(self, params: dict[str, Any]) -> dict[str, Any] | None:
        return self._get(_OBS_TABLE, _hash_params(params))

    def put_series_obs(self, params: dict[str, Any], raw: dict[str, Any]) -> None:
        self._put(_OBS_TABLE, _hash_params(params), raw, DEFAULT_TTL_OBS_S)

    def get_series_search(self, params: dict[str, Any]) -> dict[str, Any] | None:
        return self._get(_SEARCH_TABLE, _hash_params(params))

    def put_series_search(self, params: dict[str, Any], raw: dict[str, Any]) -> None:
        self._put(_SEARCH_TABLE, _hash_params(params), raw, DEFAULT_TTL_SEARCH_S)

    def get_series_meta(self, params: dict[str, Any]) -> dict[str, Any] | None:
        return self._get(_SERIES_META_TABLE, _hash_params(params))

    def put_series_meta(self, params: dict[str, Any], raw: dict[str, Any]) -> None:
        self._put(_SERIES_META_TABLE, _hash_params(params), raw, DEFAULT_TTL_SERIES_META_S)

    def get_releases(self, params: dict[str, Any]) -> dict[str, Any] | None:
        return self._get(_RELEASES_TABLE, _hash_params(params))

    def put_releases(self, params: dict[str, Any], raw: dict[str, Any]) -> None:
        self._put(_RELEASES_TABLE, _hash_params(params), raw, DEFAULT_TTL_RELEASES_S)

    # --------------------------------------------------------------- stats

    def get_stats(self) -> CacheStats:
        try:
            entries = self.backend.size()
        except Exception:
            entries = 0
        return CacheStats(
            backend=self.backend.name,
            enabled=cache_enabled(),
            entries=entries,
        )

    def reset(self) -> None:
        """Drop all rows.  Test-only convenience."""
        self.backend.clear()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_singleton: Cache | None = None
_singleton_lock = threading.Lock()


def get_cache() -> Cache | None:
    """Return the process-wide cache, or ``None`` if disabled."""
    if not cache_enabled():
        return None
    global _singleton
    if _singleton is not None:
        return _singleton
    with _singleton_lock:
        if _singleton is None:  # pragma: no branch - double-checked lock; race side not deterministically testable
            _singleton = Cache()
    return _singleton


def reset_cache_singleton() -> None:
    """Test helper — drop the singleton so the next call re-creates it."""
    global _singleton
    with _singleton_lock:
        if _singleton is not None:
            _singleton.close()
            _singleton = None
