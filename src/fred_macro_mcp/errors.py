"""Structured exception hierarchy for fred-macro-mcp.

The FRED API authenticates with a 32-character ``FRED_API_KEY`` passed as a
query parameter.  That key is the single most sensitive value in this
server, so :func:`redact_secrets` strips it (and any stray email) from
**every** rendered exception message — a leaked key in a log line or an
MCP error frame would let anyone impersonate the operator's quota.

Coverage target: **100 %**.
"""

from __future__ import annotations

import re
from typing import Final

# ---------------------------------------------------------------------------
# Redaction — strip the FRED API key and any operator email from rendered
# strings.  Order matters: redact api_key=... query params first, then any
# bare 32-char lowercase token (a FRED key shape), then emails.
# ---------------------------------------------------------------------------

_REDACTED: Final[str] = "***REDACTED***"

#: ``api_key=<value>`` inside a URL / query string (any value, any length).
_API_KEY_QS_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)(api_key=)[^&\s\"']+",
)

#: A bare FRED-shaped token: exactly 32 lowercase alphanumerics.  FRED keys
#: are documented as "32 character alpha-numeric lowercase".  Bounded with
#: word edges so we do not eat longer hashes.
_FRED_KEY_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![a-z0-9])[a-z0-9]{32}(?![a-z0-9])",
)

#: Operator email (in case it ends up in a message somewhere).
_EMAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}",
)


def redact_secrets(text: str) -> str:
    """Replace any FRED API key / email inside *text* with a placeholder.

    Idempotent and side-effect-free.  Used by every exception's ``__init__``
    so ``repr(exc)`` / ``str(exc)`` cannot leak the operator's FRED key even
    if a URL with ``api_key=...`` is interpolated into an error message.
    """
    redacted = _API_KEY_QS_RE.sub(rf"\1{_REDACTED}", text)
    redacted = _FRED_KEY_RE.sub(_REDACTED, redacted)
    return _EMAIL_RE.sub(_REDACTED, redacted)


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class FredError(Exception):
    """Base class for all fred-macro-mcp errors.

    Subclasses MUST only accept allow-listed structured fields and run all
    free-text through :func:`redact_secrets`.  This base class keeps
    ``__str__`` short so a raw ``repr(exc)`` cannot accidentally leak the
    API key.
    """

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.__class__.__name__


class FredValidationError(FredError):
    """Input validation failure (raised before any HTTP call)."""

    def __init__(self, *, field: str, reason: str) -> None:
        if not isinstance(field, str):
            raise TypeError("field must be str")
        if not isinstance(reason, str):
            raise TypeError("reason must be str")
        self.field: str = field
        self.reason: str = redact_secrets(reason)
        super().__init__(f"validation failed: {field} — {self.reason}")

    def __str__(self) -> str:
        return f"FredValidationError(field={self.field}): {self.reason}"


class FredConfigurationError(FredError):
    """The operator has not set a valid ``FRED_API_KEY``.

    FRED requires an API key on every request; without one we refuse to
    issue requests rather than receive a guaranteed 400.
    """

    def __init__(self, *, hint: str) -> None:
        if not isinstance(hint, str):
            raise TypeError("hint must be str")
        self.hint: str = redact_secrets(hint)
        super().__init__(self.hint)

    def __str__(self) -> str:
        return f"FredConfigurationError: {self.hint}"


class FredNotFoundError(FredError):
    """FRED returned 404 — the series_id / release does not exist."""

    def __init__(self, *, resource: str, hint: str) -> None:
        if not isinstance(resource, str):
            raise TypeError("resource must be str")
        if not isinstance(hint, str):
            raise TypeError("hint must be str")
        self.resource: str = redact_secrets(resource)
        self.hint: str = redact_secrets(hint)
        super().__init__(self.hint)

    def __str__(self) -> str:
        return f"FredNotFoundError(resource={self.resource}): {self.hint}"


class FredApiError(FredError):
    """FRED returned a 4xx (e.g. 400 bad_request) we treat as non-retryable.

    FRED uses HTTP 400 for invalid arguments (bad series_id, bad date) and
    HTTP 403 for a missing / invalid API key.  We surface the status plus a
    redacted hint so the agent can react without seeing the key.
    """

    def __init__(self, *, status_code: int, hint: str) -> None:
        if not isinstance(status_code, int):
            raise TypeError("status_code must be int")
        if not isinstance(hint, str):
            raise TypeError("hint must be str")
        self.status_code: int = status_code
        self.hint: str = redact_secrets(hint)
        super().__init__(self.hint)

    def __str__(self) -> str:
        return f"FredApiError(status={self.status_code}): {self.hint}"


class FredRateLimitError(FredError):
    """FRED returned 429 — the per-minute request budget was exceeded."""

    def __init__(self, *, retry_after_seconds: int, current_window_used: int) -> None:
        if not isinstance(retry_after_seconds, int):
            raise TypeError("retry_after_seconds must be int")
        if not isinstance(current_window_used, int):
            raise TypeError("current_window_used must be int")
        self.retry_after_seconds: int = retry_after_seconds
        self.current_window_used: int = current_window_used
        super().__init__(
            f"FRED rate limit exceeded; retry after {retry_after_seconds}s (used {current_window_used} in window)"
        )

    def __str__(self) -> str:
        return f"FredRateLimitError(retry_after={self.retry_after_seconds}s, window_used={self.current_window_used})"


class FredTransientError(FredError):
    """Retryable transient backend / network error (5xx, timeout, conn reset)."""

    def __init__(self, *, status_code: int, attempt: int, hint: str) -> None:
        if not isinstance(status_code, int):
            raise TypeError("status_code must be int")
        if not isinstance(attempt, int):
            raise TypeError("attempt must be int")
        if not isinstance(hint, str):
            raise TypeError("hint must be str")
        self.status_code: int = status_code
        self.attempt: int = attempt
        self.hint: str = redact_secrets(hint)
        super().__init__(self.hint)

    def __str__(self) -> str:
        return f"FredTransientError(status={self.status_code}, attempt={self.attempt}): {self.hint}"


__all__ = [
    "FredApiError",
    "FredConfigurationError",
    "FredError",
    "FredNotFoundError",
    "FredRateLimitError",
    "FredTransientError",
    "FredValidationError",
    "redact_secrets",
]
