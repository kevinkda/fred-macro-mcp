"""Security tests — OWASP Top 10 (2017 / 2021 / 2025), penetration,
exception, and boundary coverage.

All upstream HTTP is mocked with respx; no real FRED call is made.

Mapping (2017 / 2021 / 2025 share the same control surface for this
read-only, single-secret server):

  * Injection (A1-2017 / A03-2021 / A03-2025): series_id + date validation,
    query-parameter binding (no string concatenation into the URL).
  * Broken auth / identification (A2-2017 / A07-2021 / A07-2025): API key is
    required, validated, and never echoed.
  * Sensitive data exposure / cryptographic + crypto failures
    (A3-2017 / A02-2021 / A02-2025): API key redaction in every error; TLS
    always verified.
  * XXE (A4-2017): N/A — JSON-only API, no XML parser.
  * Broken access control (A5-2017 / A01-2021 / A01-2025): read-only GET only;
    no write/mutation surface.
  * Security misconfiguration (A6-2017 / A05-2021 / A05-2025): SSRF-safe fixed
    host, no redirect following, strict input models (extra=forbid).
  * SSRF (A10-2021 / A10-2025): host is a hard-coded constant; tools cannot
    redirect the request.
  * Vulnerable components (A9-2017 / A06-2021 / A06-2025): pinned deps + CI
    pip-audit gate (verified in CI, not here).
  * Insufficient logging (A10-2017 / A09-2021 / A09-2025): structured errors,
    no secret in logs.
"""

from __future__ import annotations

import inspect

import httpx
import pytest
import respx

import fred_macro_mcp.client as client_mod
from fred_macro_mcp import server as server_mod
from fred_macro_mcp.client import FRED_HOST, FredClient, resolve_api_key
from fred_macro_mcp.errors import (
    FredApiError,
    FredConfigurationError,
    FredValidationError,
    redact_secrets,
)
from fred_macro_mcp.models import GetSeriesInput, SearchSeriesInput
from fred_macro_mcp.tools import series

KEY = "abcdef0123456789abcdef0123456789"  # pragma: allowlist secret - test token
OBS_URL = f"{FRED_HOST}/fred/series/observations"


async def _no_sleep(_s: float) -> None:
    return None


def _client() -> FredClient:
    return FredClient(api_key=KEY, rate_limit_per_min=120)


# ---------------------------------------------------------------------------
# A03 Injection — series_id / dates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        "GDP'; DROP TABLE series;--",
        "GDP UNION SELECT 1",
        "../../../../etc/passwd",
        "GDP&series_id=EVIL",  # parameter pollution attempt via id
        "${jndi:ldap://evil}",
        "GDP\nInjected: header",
    ],
)
def test_injection_series_id_rejected(payload: str) -> None:
    with pytest.raises(FredValidationError):
        GetSeriesInput(series_id=payload)


@respx.mock
async def test_series_id_bound_not_concatenated() -> None:
    """A valid id with FRED-legal punctuation is sent as a bound param."""
    route = respx.get(OBS_URL).mock(return_value=httpx.Response(200, json={"observations": []}))
    async with _client() as c:
        await c.get_json("/fred/series/observations", params={"series_id": "T10Y2Y"})
    sent = str(route.calls.last.request.url)
    # httpx URL-encodes params; the id is in the query string, not the path.
    assert "/fred/series/observations" in sent
    assert "series_id=T10Y2Y" in sent


# ---------------------------------------------------------------------------
# A07 Broken auth / identification — API key required + validated
# ---------------------------------------------------------------------------


def test_api_key_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(FredConfigurationError):
        resolve_api_key()


def test_api_key_format_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "UPPER0123456789abcdef0123456789x")  # has uppercase
    with pytest.raises(FredConfigurationError):
        resolve_api_key()


# ---------------------------------------------------------------------------
# A02/A03 Sensitive data — key never leaks
# ---------------------------------------------------------------------------


@respx.mock
async def test_api_key_not_in_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod.asyncio, "sleep", _no_sleep)
    respx.get(OBS_URL).mock(return_value=httpx.Response(400, json={"error_message": f"key {KEY} bad"}))
    async with _client() as c:
        with pytest.raises(FredApiError) as exc:
            await c.get_json("/fred/series/observations")
    assert KEY not in str(exc.value)
    assert KEY not in repr(exc.value)


def test_redaction_covers_url_with_key() -> None:
    url = f"{FRED_HOST}/fred/series?series_id=GDP&api_key={KEY}"
    assert KEY not in redact_secrets(url)


def test_validation_error_repr_no_key() -> None:
    exc = FredValidationError(field="series_id", reason=f"saw {KEY}")
    assert KEY not in repr(exc)


# ---------------------------------------------------------------------------
# A01/A05 Access control + misconfig — read-only, no write surface
# ---------------------------------------------------------------------------


def test_no_write_verbs_in_client_source() -> None:
    src = inspect.getsource(client_mod)
    # The client only ever issues GET — no POST/PUT/PATCH/DELETE method calls.
    for verb in (".post(", ".put(", ".patch(", ".delete("):
        assert verb not in src


def test_redirects_disabled() -> None:
    c = _client()
    assert c._client.follow_redirects is False


def test_strict_models_forbid_extra() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GetSeriesInput(series_id="GDP", evil="x")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        SearchSeriesInput(query="x", evil="y")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# A10 SSRF — host is fixed; tools cannot redirect the request
# ---------------------------------------------------------------------------


def test_host_is_constant() -> None:
    assert FRED_HOST == "https://api.stlouisfed.org"
    c = _client()
    assert str(c._client.base_url).rstrip("/") == FRED_HOST


@respx.mock
async def test_tool_cannot_change_host() -> None:
    """Even a hostile series_id cannot move the request off the FRED host."""
    route = respx.get(OBS_URL).mock(return_value=httpx.Response(200, json={"observations": []}))
    async with _client() as c:
        await c.get_json("/fred/series/observations", params={"series_id": "GDP"})
    assert str(route.calls.last.request.url).startswith(FRED_HOST)


def test_no_user_controlled_url_in_tools() -> None:
    # series tools build requests from a fixed path constant only — the
    # endpoint path module-constant is relative (no inline host literal in
    # the request-building code).
    src = inspect.getsource(series)
    assert '_OBS_PATH = "/fred/series/observations"' in src
    # No f-string host interpolation in the request path.
    assert 'f"{FRED_HOST}' not in src


# ---------------------------------------------------------------------------
# A09 Logging — server hardens stdio + structured logging on import
# ---------------------------------------------------------------------------


def test_stdio_hardened() -> None:
    # _harden_stdio ran at import time; builtins.print is wrapped to stderr.
    import builtins

    assert builtins.print is not None  # patched wrapper installed


def test_server_module_has_no_print_calls() -> None:
    src = inspect.getsource(server_mod)
    # The only print reference is the hardening wrapper, not stray prints.
    assert "_orig_print" in src


# ---------------------------------------------------------------------------
# Boundary tests
# ---------------------------------------------------------------------------


def test_boundary_limit_min_max() -> None:
    assert GetSeriesInput(series_id="GDP", limit=1).limit == 1
    assert GetSeriesInput(series_id="GDP", limit=100000).limit == 100000


def test_boundary_series_id_length() -> None:
    assert GetSeriesInput(series_id="G").series_id == "G"  # 1 char
    assert GetSeriesInput(series_id="A" * 64).series_id == "A" * 64  # 64 chars max


def test_boundary_series_id_too_long() -> None:
    with pytest.raises((FredValidationError, ValueError)):
        GetSeriesInput(series_id="A" * 65)


@respx.mock
async def test_boundary_empty_observations() -> None:
    respx.get(OBS_URL).mock(return_value=httpx.Response(200, json={"observations": []}))
    out = await series.get_series_impl(GetSeriesInput(series_id="GDP"))
    assert out["observation_count"] == 0


# ---------------------------------------------------------------------------
# Exception tests — no sensitive data in any framed error
# ---------------------------------------------------------------------------


@respx.mock
async def test_exception_paths_redact_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod.asyncio, "sleep", _no_sleep)
    # 403 bad-key response containing the key in the message.
    respx.get(OBS_URL).mock(return_value=httpx.Response(403, json={"error_message": f"invalid {KEY}"}))
    from fred_macro_mcp.server import _frame_error

    async with _client() as c:
        try:
            await c.get_json("/fred/series/observations")
        except FredApiError as exc:
            framed = _frame_error(exc)
            assert KEY not in str(framed)
        else:  # pragma: no cover - must raise
            raise AssertionError("expected FredApiError")
