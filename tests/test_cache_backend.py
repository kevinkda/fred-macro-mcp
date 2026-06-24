"""Tests for the pluggable cache backends.

Three paths (v0.7-roadmap §4 acceptance):
  1. MemoryBackend get/set/TTL/LRU/concurrency.
  2. ClickHouseBackend with a mock client (no real ClickHouse).
  3. No-ClickHouse degradation (requires_clickhouse_persistence) + factory.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

import fred_macro_mcp.cache_backend as cb
from fred_macro_mcp.cache_backend import (
    ClickHouseBackend,
    ClickHouseNotInstalledError,
    MemoryBackend,
    get_cache_backend,
    requires_clickhouse_signal,
)

# ---------------------------------------------------------------------------
# MemoryBackend — response cache
# ---------------------------------------------------------------------------


def test_memory_set_get() -> None:
    be = MemoryBackend()
    be.set("t", "k", {"v": 1}, 60)
    assert be.get("t", "k") == {"v": 1}
    assert be.size() == 1


def test_memory_miss() -> None:
    assert MemoryBackend().get("t", "absent") is None


def test_memory_ttl_expiry() -> None:
    be = MemoryBackend()
    be.set("t", "k", {"v": 1}, 0)  # immediate expiry
    time.sleep(0.001)
    assert be.get("t", "k") is None
    assert be.size() == 0  # purged on read


def test_memory_deepcopy_isolation() -> None:
    be = MemoryBackend()
    payload: dict[str, Any] = {"nested": {"a": 1}}
    be.set("t", "k", payload, 60)
    payload["nested"]["a"] = 999  # mutate caller copy
    got = be.get("t", "k")
    assert got is not None
    assert got["nested"]["a"] == 1  # cache unaffected
    got["nested"]["a"] = 7  # mutate returned copy
    again = be.get("t", "k")
    assert again is not None
    assert again["nested"]["a"] == 1


def test_memory_lru_eviction() -> None:
    be = MemoryBackend(maxsize=2)
    be.set("t", "a", {"i": 1}, 60)
    be.set("t", "b", {"i": 2}, 60)
    be.get("t", "a")  # touch a -> b is now LRU
    be.set("t", "c", {"i": 3}, 60)  # evicts b
    assert be.get("t", "b") is None
    assert be.get("t", "a") == {"i": 1}
    assert be.get("t", "c") == {"i": 3}


def test_memory_unbounded() -> None:
    be = MemoryBackend(maxsize=0)
    for i in range(50):
        be.set("t", str(i), {"i": i}, 60)
    assert be.size() == 50


def test_memory_overwrite_moves_to_end() -> None:
    be = MemoryBackend(maxsize=2)
    be.set("t", "a", {"i": 1}, 60)
    be.set("t", "b", {"i": 2}, 60)
    be.set("t", "a", {"i": 10}, 60)  # overwrite a, moves to end
    be.set("t", "c", {"i": 3}, 60)  # evicts b (oldest)
    assert be.get("t", "b") is None
    assert be.get("t", "a") == {"i": 10}


def test_memory_clear() -> None:
    be = MemoryBackend()
    be.set("t", "k", {"v": 1}, 60)
    be.clear()
    assert be.size() == 0


def test_memory_concurrency_safe() -> None:
    be = MemoryBackend(maxsize=0)
    errors: list[Exception] = []

    def worker(n: int) -> None:
        try:
            for i in range(200):
                be.set("t", f"{n}-{i}", {"i": i}, 60)
                be.get("t", f"{n}-{i}")
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors
    assert be.size() == 8 * 200


# ---------------------------------------------------------------------------
# MemoryBackend — time-series degradation
# ---------------------------------------------------------------------------


def test_memory_append_timeseries_degrades() -> None:
    sig = MemoryBackend().append_timeseries("s", {"x": 1})
    assert sig["status"] == "requires_clickhouse_persistence"
    assert "clickhouse" in sig["hint"].lower()


def test_memory_query_timeseries_degrades() -> None:
    sig = MemoryBackend().query_timeseries("s", {"limit": 10})
    assert sig["status"] == "requires_clickhouse_persistence"


def test_requires_signal_shape() -> None:
    sig = requires_clickhouse_signal()
    assert set(sig) == {"status", "hint"}


# ---------------------------------------------------------------------------
# ClickHouse — not installed
# ---------------------------------------------------------------------------


def test_clickhouse_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> Any:
        raise ClickHouseNotInstalledError(ImportError("no module"))

    monkeypatch.setattr(cb, "_import_clickhouse_connect", boom)
    monkeypatch.setenv("FRED_CLICKHOUSE_URL", "clickhouse://h:8123")
    with pytest.raises(ClickHouseNotInstalledError) as exc:
        ClickHouseBackend()
    assert "clickhouse-connect" in exc.value.hint


def test_clickhouse_real_import_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # Exercise the real lazy import helper: clickhouse_connect is NOT in the
    # default install, so this must raise ClickHouseNotInstalledError.
    with pytest.raises(ClickHouseNotInstalledError):
        cb._import_clickhouse_connect()


def test_clickhouse_missing_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # import succeeds (mock module) but URL is unset -> raises.
    fake_module = type("M", (), {"get_client": staticmethod(lambda **_: object())})
    monkeypatch.setattr(cb, "_import_clickhouse_connect", lambda: fake_module)
    monkeypatch.delenv("FRED_CLICKHOUSE_URL", raising=False)
    with pytest.raises(ClickHouseNotInstalledError):
        ClickHouseBackend(url="")


# ---------------------------------------------------------------------------
# ClickHouse — mock client
# ---------------------------------------------------------------------------


class FakeResult:
    def __init__(self, rows: list[list[Any]]) -> None:
        self.result_rows = rows


class FakeClient:
    """In-memory stand-in for clickhouse_connect's client."""

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.response_rows: list[list[Any]] = []
        self.timeseries_rows: list[list[Any]] = []
        self.fail_get = False
        self.fail_insert = False
        self.fail_command = False

    def command(self, sql: str) -> None:
        if self.fail_command:
            raise RuntimeError("command boom")
        self.commands.append(sql)

    def insert(self, table: str, rows: list[list[Any]], *, column_names: list[str]) -> None:
        if self.fail_insert:
            raise RuntimeError("insert boom")
        if table.endswith("response_cache"):
            self.response_rows.extend(rows)
        else:
            self.timeseries_rows.extend(rows)

    def query(self, sql: str, *, parameters: dict[str, Any] | None = None) -> FakeResult:
        if self.fail_get:
            raise RuntimeError("query boom")
        if "count()" in sql:
            return FakeResult([[len(self.response_rows)]])
        if "fred_timeseries" in sql:
            return FakeResult([[r[1]] for r in self.timeseries_rows])
        # response cache lookup: return the most-recent matching raw_json
        assert parameters is not None
        for row in reversed(self.response_rows):
            if row[0] == parameters["t"] and row[1] == parameters["k"]:
                return FakeResult([[row[2]]])
        return FakeResult([])


@pytest.fixture
def ch_backend() -> ClickHouseBackend:
    client = FakeClient()
    return ClickHouseBackend(url="clickhouse://h:8123", client=client)


def test_clickhouse_schema_bootstrapped(ch_backend: ClickHouseBackend) -> None:
    assert ch_backend.name == "clickhouse"
    assert len(ch_backend._client.commands) == 2  # type: ignore[attr-defined]


def test_clickhouse_set_get(ch_backend: ClickHouseBackend) -> None:
    ch_backend.set("t", "k", {"v": 42}, 600)
    assert ch_backend.get("t", "k") == {"v": 42}
    assert ch_backend.size() == 1


def test_clickhouse_get_miss(ch_backend: ClickHouseBackend) -> None:
    assert ch_backend.get("t", "absent") is None


def test_clickhouse_get_non_dict(ch_backend: ClickHouseBackend) -> None:
    client: FakeClient = ch_backend._client  # type: ignore[assignment]
    client.response_rows.append(["t", "k", "[1, 2, 3]"])  # JSON list, not dict
    assert ch_backend.get("t", "k") is None


def test_clickhouse_get_failure_degrades(ch_backend: ClickHouseBackend) -> None:
    ch_backend._client.fail_get = True  # type: ignore[attr-defined]
    assert ch_backend.get("t", "k") is None


def test_clickhouse_set_failure_swallowed(ch_backend: ClickHouseBackend) -> None:
    ch_backend._client.fail_insert = True  # type: ignore[attr-defined]
    ch_backend.set("t", "k", {"v": 1}, 60)  # no raise


def test_clickhouse_timeseries_roundtrip(ch_backend: ClickHouseBackend) -> None:
    res = ch_backend.append_timeseries("iv", {"d": "2024-01-01", "v": 1.0})
    assert res["status"] == "ok"
    q = ch_backend.query_timeseries("iv", {"limit": 10})
    assert q["status"] == "ok"
    assert q["rows"] == [{"d": "2024-01-01", "v": 1.0}]


def test_clickhouse_append_failure(ch_backend: ClickHouseBackend) -> None:
    ch_backend._client.fail_insert = True  # type: ignore[attr-defined]
    res = ch_backend.append_timeseries("iv", {"v": 1})
    assert res["status"] == "error"


def test_clickhouse_query_failure(ch_backend: ClickHouseBackend) -> None:
    ch_backend._client.fail_get = True  # type: ignore[attr-defined]
    res = ch_backend.query_timeseries("iv", {})
    assert res["status"] == "error"


def test_clickhouse_clear(ch_backend: ClickHouseBackend) -> None:
    ch_backend.set("t", "k", {"v": 1}, 60)
    ch_backend.clear()
    # TRUNCATE issued via command()
    assert any("TRUNCATE" in c for c in ch_backend._client.commands)  # type: ignore[attr-defined]


def test_clickhouse_clear_failure(ch_backend: ClickHouseBackend) -> None:
    ch_backend._client.fail_command = True  # type: ignore[attr-defined]
    ch_backend.clear()  # no raise


def test_clickhouse_size_failure(ch_backend: ClickHouseBackend) -> None:
    ch_backend._client.fail_get = True  # type: ignore[attr-defined]
    assert ch_backend.size() == 0


def test_clickhouse_size_empty(ch_backend: ClickHouseBackend) -> None:
    client: FakeClient = ch_backend._client  # type: ignore[assignment]

    def empty_query(sql: str, *, parameters: dict[str, Any] | None = None) -> FakeResult:
        return FakeResult([])

    client.query = empty_query  # type: ignore[method-assign]
    assert ch_backend.size() == 0


def test_clickhouse_schema_bootstrap_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    client.fail_command = True
    # Should not raise even though DDL command fails (best-effort).
    be = ClickHouseBackend(url="clickhouse://h:8123", client=client)
    assert be.name == "clickhouse"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_factory_default_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRED_CACHE_BACKEND", raising=False)
    assert isinstance(get_cache_backend(), MemoryBackend)


def test_factory_unknown_falls_back_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_CACHE_BACKEND", "redis")
    assert isinstance(get_cache_backend(), MemoryBackend)


def test_factory_clickhouse(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = FakeClient()
    fake_module = type("M", (), {"get_client": staticmethod(lambda **_: fake_client)})
    monkeypatch.setattr(cb, "_import_clickhouse_connect", lambda: fake_module)
    monkeypatch.setenv("FRED_CACHE_BACKEND", "clickhouse")
    monkeypatch.setenv("FRED_CLICKHOUSE_URL", "clickhouse://h:8123")
    be = get_cache_backend()
    assert isinstance(be, ClickHouseBackend)


def test_clickhouse_connect_builds_client(monkeypatch: pytest.MonkeyPatch) -> None:
    # Exercise the _connect() path with a mocked module (no real CH).
    captured: dict[str, Any] = {}

    def get_client(**kwargs: Any) -> FakeClient:
        captured.update(kwargs)
        return FakeClient()

    fake_module = type("M", (), {"get_client": staticmethod(get_client)})
    monkeypatch.setattr(cb, "_import_clickhouse_connect", lambda: fake_module)
    be = ClickHouseBackend(url="clickhouse://user:pw@h:8123/db")  # pragma: allowlist secret - test DSN
    assert be.name == "clickhouse"
    assert captured["dsn"] == "clickhouse://user:pw@h:8123/db"  # pragma: allowlist secret - test DSN
