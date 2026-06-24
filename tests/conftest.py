"""Shared pytest fixtures and helpers for fred-macro-mcp tests.

All HTTP is mocked with ``respx`` — no real FRED API call is ever made.
The test API key is a deterministic 32-char lowercase token (valid shape,
not a real credential).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator

import pytest

import fred_macro_mcp.cache as cache_mod
import fred_macro_mcp.tools._runtime as runtime_mod

# A syntactically-valid FRED key (32 lowercase alphanumerics) — NOT real.
TEST_API_KEY = "abcdef0123456789abcdef0123456789"  # pragma: allowlist secret - test fixture


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Isolate env + cache/client singletons per test."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("FRED_CACHE_BYPASS", raising=False)
    monkeypatch.delenv("FRED_CACHE_BACKEND", raising=False)
    monkeypatch.delenv("FRED_CLICKHOUSE_URL", raising=False)
    monkeypatch.delenv("FRED_RATE_LIMIT_PER_MIN", raising=False)
    monkeypatch.setenv("FRED_CACHE_ENABLED", "1")
    monkeypatch.setenv("FRED_API_KEY", TEST_API_KEY)
    cache_mod.reset_cache_singleton()
    runtime_mod.reset_client_cache()
    yield
    cache_mod.reset_cache_singleton()
    runtime_mod.reset_client_cache()


def pytest_configure(config: pytest.Config) -> None:
    # Make sure the CWD doesn't pollute test runs.
    os.environ.pop("FRED_RATE_LIMIT_PER_MIN", None)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if sys.platform != "win32":
        return
    skip_posix = pytest.mark.skip(reason="POSIX-only test")
    for item in items:
        if "posix_only" in item.keywords:
            item.add_marker(skip_posix)
