"""Pydantic v2 input schemas for every outward-facing tool.

The critical validated input is ``series_id`` — FRED series identifiers are
uppercase alphanumerics with a small punctuation set (e.g. ``GDP``,
``CPIAUCSL``, ``DGS10``, ``T10Y2Y``, ``UNRATE``).  We validate it against an
anchored regex so a malicious value can never be smuggled into the request
(it is passed to FRED only as a bound query parameter, never string-
concatenated into a URL).

Dates are validated as ISO ``YYYY-MM-DD``.  Search queries are length-bounded
free text.

Coverage target: **100 %**.
"""

from __future__ import annotations

import re
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

# ---------------------------------------------------------------------------
# Regexes — anchored to prevent partial-match Pydantic search semantics.
# ---------------------------------------------------------------------------

#: FRED series id: 1-64 chars, uppercase letters / digits / ``. _ -`` / ``&``.
#: FRED ids are uppercase (e.g. GDP, CPIAUCSL, DGS10, T10Y2Y, A191RL1Q225SBEA).
SERIES_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z0-9][A-Z0-9._&\-]{0,63}$")

#: ISO date ``YYYY-MM-DD`` (calendar-validity is enforced by ``date.fromisoformat``).
ISO_DATE_RE: Final[re.Pattern[str]] = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# Constrained string types
# ---------------------------------------------------------------------------

SeriesId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
    ),
]

SearchText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=200,
    ),
]


def _validate_series_id(v: object) -> object:
    """Uppercase + regex-validate a series id (mode='before' validator)."""
    if isinstance(v, str):
        v = v.strip().upper()
        if not SERIES_ID_RE.match(v):
            from .errors import FredValidationError

            raise FredValidationError(
                field="series_id",
                reason=f"must match {SERIES_ID_RE.pattern}",
            )
    return v


def _validate_iso_date(field_name: str, v: object) -> object:
    """Validate an optional ISO date string (``YYYY-MM-DD``)."""
    if v is None:
        return None
    if isinstance(v, str):
        candidate = v.strip()
        if not ISO_DATE_RE.match(candidate):
            from .errors import FredValidationError

            raise FredValidationError(
                field=field_name,
                reason="must be an ISO date in YYYY-MM-DD form",
            )
        from datetime import date

        try:
            date.fromisoformat(candidate)
        except ValueError as exc:
            from .errors import FredValidationError

            raise FredValidationError(
                field=field_name,
                reason="is not a valid calendar date",
            ) from exc
        return candidate
    return v


# ---------------------------------------------------------------------------
# Base — strict-by-default mixin
# ---------------------------------------------------------------------------


class _BaseInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        frozen=True,
    )


# ---------------------------------------------------------------------------
# Concrete schemas — one per tool.
# ---------------------------------------------------------------------------


class GetSeriesInput(_BaseInput):
    """Input for ``get_series`` — an observation window for one series."""

    series_id: SeriesId
    start: str | None = None
    end: str | None = None
    limit: int = Field(default=100000, ge=1, le=100000)

    @field_validator("series_id", mode="before")
    @classmethod
    def _v_series_id(cls, v: object) -> object:
        return _validate_series_id(v)

    @field_validator("start", mode="before")
    @classmethod
    def _v_start(cls, v: object) -> object:
        return _validate_iso_date("start", v)

    @field_validator("end", mode="before")
    @classmethod
    def _v_end(cls, v: object) -> object:
        return _validate_iso_date("end", v)


class SearchSeriesInput(_BaseInput):
    """Input for ``search_series`` — keyword search across the FRED catalog."""

    query: SearchText
    limit: int = Field(default=25, ge=1, le=1000)

    @field_validator("query", mode="before")
    @classmethod
    def _strip_query(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip()
        return v


class GetSeriesLatestInput(_BaseInput):
    """Input for ``get_series_latest`` — the single most-recent observation."""

    series_id: SeriesId

    @field_validator("series_id", mode="before")
    @classmethod
    def _v_series_id(cls, v: object) -> object:
        return _validate_series_id(v)


class GetReleaseCalendarInput(_BaseInput):
    """Input for ``get_release_calendar`` — upcoming FRED release dates."""

    days: int = Field(default=14, ge=1, le=180)


class HealthCheckInput(_BaseInput):
    """Input for ``health_check`` — empty."""


class GetServerInfoInput(_BaseInput):
    """Input for ``get_server_info`` — empty."""


# ---------------------------------------------------------------------------
# Tool registry — lets ``get_server_info`` enumerate tools without importing
# the server module (avoids a circular import in __init__).
# ---------------------------------------------------------------------------

_SUPPORTED_TOOLS: Final[tuple[str, ...]] = (
    "get_series",
    "search_series",
    "get_series_latest",
    "get_release_calendar",
    "health_check",
    "get_server_info",
)


def supported_tool_names() -> list[str]:
    """Stable list of tool names the server exposes."""
    return list(_SUPPORTED_TOOLS)


__all__ = [
    "ISO_DATE_RE",
    "SERIES_ID_RE",
    "GetReleaseCalendarInput",
    "GetSeriesInput",
    "GetSeriesLatestInput",
    "GetServerInfoInput",
    "HealthCheckInput",
    "SearchSeriesInput",
    "SearchText",
    "SeriesId",
    "supported_tool_names",
]
