"""Tests for the structured exception hierarchy + secret redaction.

Covers happy paths, type-guard branches, and the API-key / email redaction
that protects every rendered message (OWASP A3/A09 — sensitive data
exposure + logging).
"""

from __future__ import annotations

import pytest

from fred_macro_mcp.errors import (
    FredApiError,
    FredConfigurationError,
    FredError,
    FredNotFoundError,
    FredRateLimitError,
    FredTransientError,
    FredValidationError,
    redact_secrets,
)

REAL_SHAPE_KEY = "abcdef0123456789abcdef0123456789"  # pragma: allowlist secret  # gitleaks:allow


# ---------------------------------------------------------------------------
# redact_secrets
# ---------------------------------------------------------------------------


def test_redact_api_key_query_param() -> None:
    url = f"https://api.stlouisfed.org/fred/series?series_id=GDP&api_key={REAL_SHAPE_KEY}&file_type=json"
    out = redact_secrets(url)
    assert REAL_SHAPE_KEY not in out
    assert "api_key=***REDACTED***" in out


def test_redact_bare_32char_key() -> None:
    out = redact_secrets(f"key leaked: {REAL_SHAPE_KEY} end")
    assert REAL_SHAPE_KEY not in out
    assert "***REDACTED***" in out


def test_redact_email() -> None:
    out = redact_secrets("contact me at alice@example.com please")
    assert "alice@example.com" not in out
    assert "***REDACTED***" in out


def test_redact_is_idempotent() -> None:
    once = redact_secrets(f"api_key={REAL_SHAPE_KEY}")
    twice = redact_secrets(once)
    assert once == twice


def test_redact_leaves_clean_text() -> None:
    assert redact_secrets("nothing sensitive here") == "nothing sensitive here"


# ---------------------------------------------------------------------------
# FredError base
# ---------------------------------------------------------------------------


def test_base_str() -> None:
    assert str(FredError()) == "FredError"


# ---------------------------------------------------------------------------
# FredValidationError
# ---------------------------------------------------------------------------


def test_validation_error_ok() -> None:
    exc = FredValidationError(field="series_id", reason="bad")
    assert exc.field == "series_id"
    assert "series_id" in str(exc)


def test_validation_error_redacts_reason() -> None:
    exc = FredValidationError(field="x", reason=f"leaked {REAL_SHAPE_KEY}")
    assert REAL_SHAPE_KEY not in str(exc)


def test_validation_error_type_guards() -> None:
    with pytest.raises(TypeError):
        FredValidationError(field=1, reason="x")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        FredValidationError(field="x", reason=2)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# FredConfigurationError
# ---------------------------------------------------------------------------


def test_configuration_error_ok() -> None:
    exc = FredConfigurationError(hint="set the key")
    assert "set the key" in str(exc)


def test_configuration_error_type_guard() -> None:
    with pytest.raises(TypeError):
        FredConfigurationError(hint=1)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# FredNotFoundError
# ---------------------------------------------------------------------------


def test_not_found_ok() -> None:
    exc = FredNotFoundError(resource="/fred/series", hint="404")
    assert "/fred/series" in str(exc)


def test_not_found_redacts() -> None:
    exc = FredNotFoundError(
        resource=f"/fred/series?api_key={REAL_SHAPE_KEY}",
        hint=f"404 for api_key={REAL_SHAPE_KEY}",
    )
    assert REAL_SHAPE_KEY not in str(exc)


def test_not_found_type_guards() -> None:
    with pytest.raises(TypeError):
        FredNotFoundError(resource=1, hint="x")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        FredNotFoundError(resource="x", hint=2)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# FredApiError
# ---------------------------------------------------------------------------


def test_api_error_ok() -> None:
    exc = FredApiError(status_code=400, hint="bad argument")
    assert exc.status_code == 400
    assert "400" in str(exc)


def test_api_error_redacts() -> None:
    exc = FredApiError(status_code=403, hint=f"api_key={REAL_SHAPE_KEY}")
    assert REAL_SHAPE_KEY not in str(exc)


def test_api_error_type_guards() -> None:
    with pytest.raises(TypeError):
        FredApiError(status_code="x", hint="y")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        FredApiError(status_code=400, hint=2)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# FredRateLimitError
# ---------------------------------------------------------------------------


def test_rate_limit_ok() -> None:
    exc = FredRateLimitError(retry_after_seconds=5, current_window_used=120)
    assert exc.retry_after_seconds == 5
    assert "retry_after=5s" in str(exc)


def test_rate_limit_type_guards() -> None:
    with pytest.raises(TypeError):
        FredRateLimitError(retry_after_seconds="x", current_window_used=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        FredRateLimitError(retry_after_seconds=1, current_window_used="x")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# FredTransientError
# ---------------------------------------------------------------------------


def test_transient_ok() -> None:
    exc = FredTransientError(status_code=503, attempt=2, hint="upstream")
    assert exc.status_code == 503
    assert "attempt=2" in str(exc)


def test_transient_redacts() -> None:
    exc = FredTransientError(status_code=500, attempt=0, hint=f"x {REAL_SHAPE_KEY}")
    assert REAL_SHAPE_KEY not in str(exc)


def test_transient_type_guards() -> None:
    with pytest.raises(TypeError):
        FredTransientError(status_code="x", attempt=0, hint="y")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        FredTransientError(status_code=500, attempt="x", hint="y")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        FredTransientError(status_code=500, attempt=0, hint=2)  # type: ignore[arg-type]
