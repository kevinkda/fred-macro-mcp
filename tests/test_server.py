"""Server integration tests — exercise the FastMCP wiring + error framing.

We exercise the wired tool callables via the in-process FastMCP app
object; those are the same coroutines exposed on stdio.  All upstream HTTP
is mocked with respx.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

import fred_macro_mcp.tools._runtime as runtime_mod
from fred_macro_mcp.client import FRED_HOST, FredClient
from fred_macro_mcp.errors import (
    FredApiError,
    FredConfigurationError,
    FredError,
    FredNotFoundError,
    FredRateLimitError,
    FredTransientError,
    FredValidationError,
)
from fred_macro_mcp.server import SERVER_VERSION, _frame_error, app

KEY = "abcdef0123456789abcdef0123456789"  # pragma: allowlist secret - test token
OBS_URL = f"{FRED_HOST}/fred/series/observations"
SEARCH_URL = f"{FRED_HOST}/fred/series/search"
RELEASES_URL = f"{FRED_HOST}/fred/releases/dates"


def _extract_payload(result: Any) -> dict[str, Any]:
    """Pull the structured dict out of FastMCP's call_tool return."""
    if isinstance(result, tuple):
        for item in result:
            if isinstance(item, dict):
                return item
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    raise AssertionError(f"could not extract payload from {result!r}")


@pytest.fixture(autouse=True)
async def _inject_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", KEY)
    await runtime_mod.set_client_for_tests(FredClient(api_key=KEY, rate_limit_per_min=120))


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


async def test_app_exports_six_tools() -> None:
    tools = await app().list_tools()
    names = {t.name for t in tools}
    assert names == {
        "get_series",
        "search_series",
        "get_series_latest",
        "get_release_calendar",
        "health_check",
        "get_server_info",
    }


def test_initialize_reports_release_tag_version() -> None:
    from fred_macro_mcp import __version__ as expected

    init = app()._mcp_server.create_initialization_options()
    assert init.server_name == "fred-macro-mcp"
    assert init.server_version == expected


def test_app_is_singleton() -> None:
    assert app() is app()


# ---------------------------------------------------------------------------
# Tool calls through the app
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_series_through_app() -> None:
    respx.get(OBS_URL).mock(
        return_value=httpx.Response(200, json={"observations": [{"date": "2024-01-01", "value": "1.0"}]})
    )
    result = await app().call_tool("get_series", {"series_id": "GDP"})
    payload = _extract_payload(result)
    assert payload["series_id"] == "GDP"


@respx.mock
async def test_search_through_app() -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"seriess": []}))
    result = await app().call_tool("search_series", {"query": "gdp"})
    payload = _extract_payload(result)
    assert payload["query"] == "gdp"


@respx.mock
async def test_latest_through_app() -> None:
    respx.get(OBS_URL).mock(
        return_value=httpx.Response(200, json={"observations": [{"date": "2024-05-01", "value": "3.9"}]})
    )
    result = await app().call_tool("get_series_latest", {"series_id": "UNRATE"})
    payload = _extract_payload(result)
    assert payload["latest"]["value"] == 3.9


@respx.mock
async def test_calendar_through_app() -> None:
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json={"release_dates": []}))
    result = await app().call_tool("get_release_calendar", {"days": 7})
    payload = _extract_payload(result)
    assert payload["days"] == 7


async def test_health_through_app() -> None:
    result = await app().call_tool("health_check", {})
    payload = _extract_payload(result)
    assert "rate_limit_hard_cap" in payload


async def test_server_info_through_app() -> None:
    result = await app().call_tool("get_server_info", {})
    payload = _extract_payload(result)
    assert payload["server_version"] == SERVER_VERSION
    assert len(payload["supported_tools"]) == 6


# ---------------------------------------------------------------------------
# Error framing — every tool catches FredError and returns an envelope
# ---------------------------------------------------------------------------


async def test_get_series_validation_framed() -> None:
    result = await app().call_tool("get_series", {"series_id": "bad id!"})
    payload = _extract_payload(result)
    assert payload["error"] == "validation"
    assert payload["field"] == "series_id"


async def test_search_validation_framed() -> None:
    # A query exceeding max_length trips a Pydantic ValidationError, which is
    # NOT a FredError — so it propagates as a FastMCP ToolError rather than a
    # framed envelope.  Confirm the over-long input is rejected, not silently
    # forwarded to FRED.
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises(ToolError):
        await app().call_tool("search_series", {"query": "x" * 201})


@respx.mock
async def test_latest_not_found_framed() -> None:
    respx.get(OBS_URL).mock(return_value=httpx.Response(404))
    result = await app().call_tool("get_series_latest", {"series_id": "NOPE"})
    payload = _extract_payload(result)
    assert payload["error"] == "not_found"


@respx.mock
async def test_calendar_api_error_framed() -> None:
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(400, json={"error_message": "bad days"}))
    result = await app().call_tool("get_release_calendar", {"days": 7})
    payload = _extract_payload(result)
    assert payload["error"] == "api_error"


@respx.mock
async def test_search_transient_framed(monkeypatch: pytest.MonkeyPatch) -> None:
    import fred_macro_mcp.client as client_mod

    async def _no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(client_mod.asyncio, "sleep", _no_sleep)
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(503))
    result = await app().call_tool("search_series", {"query": "gdp"})
    payload = _extract_payload(result)
    assert payload["error"] == "transient"


# ---------------------------------------------------------------------------
# _frame_error unit coverage (every branch)
# ---------------------------------------------------------------------------


def test_frame_validation() -> None:
    out = _frame_error(FredValidationError(field="f", reason="r"))
    assert out == {"error": "validation", "field": "f", "reason": "r"}


def test_frame_configuration() -> None:
    out = _frame_error(FredConfigurationError(hint="h"))
    assert out["error"] == "configuration"


def test_frame_not_found() -> None:
    out = _frame_error(FredNotFoundError(resource="r", hint="h"))
    assert out["error"] == "not_found"


def test_frame_rate_limit() -> None:
    out = _frame_error(FredRateLimitError(retry_after_seconds=1, current_window_used=2))
    assert out["error"] == "rate_limit"


def test_frame_api_error() -> None:
    out = _frame_error(FredApiError(status_code=400, hint="h"))
    assert out["error"] == "api_error"


def test_frame_transient() -> None:
    out = _frame_error(FredTransientError(status_code=500, attempt=0, hint="h"))
    assert out["error"] == "transient"


def test_frame_base_fred_error() -> None:
    out = _frame_error(FredError())
    assert out["error"] == "fred_error"


def test_frame_internal() -> None:
    out = _frame_error(ValueError("boom"))
    assert out == {"error": "internal", "type": "ValueError"}


# ---------------------------------------------------------------------------
# main / entry point
# ---------------------------------------------------------------------------


def test_main_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    import fred_macro_mcp.server as server_mod

    called: list[bool] = []

    class FakeApp:
        def run(self) -> None:
            called.append(True)

    monkeypatch.setattr(server_mod, "app", lambda: FakeApp())
    server_mod.main()
    assert called == [True]
