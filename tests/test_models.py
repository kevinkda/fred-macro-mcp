"""Tests for Pydantic v2 input schemas + validators (OWASP A03 injection,
boundary testing).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fred_macro_mcp.errors import FredValidationError
from fred_macro_mcp.models import (
    GetReleaseCalendarInput,
    GetSeriesInput,
    GetSeriesLatestInput,
    SearchSeriesInput,
    supported_tool_names,
)

# ---------------------------------------------------------------------------
# GetSeriesInput
# ---------------------------------------------------------------------------


def test_get_series_minimal() -> None:
    args = GetSeriesInput(series_id="gdp")
    assert args.series_id == "GDP"  # uppercased
    assert args.start is None
    assert args.limit == 100000


def test_get_series_full() -> None:
    args = GetSeriesInput(series_id="CPIAUCSL", start="2024-01-01", end="2024-12-31", limit=10)
    assert args.start == "2024-01-01"
    assert args.end == "2024-12-31"
    assert args.limit == 10


def test_get_series_id_punctuation_ok() -> None:
    # FRED ids carry dots/dashes/ampersands.
    assert GetSeriesInput(series_id="T10Y2Y").series_id == "T10Y2Y"
    assert GetSeriesInput(series_id="A191RL1Q225SBEA").series_id == "A191RL1Q225SBEA"


@pytest.mark.parametrize(
    "bad",
    [
        "GDP; DROP TABLE",  # injection attempt
        "../../etc/passwd",  # path traversal
        "GDP OR 1=1",  # space rejected
        "<script>",
        "a" * 65,  # too long
    ],
)
def test_get_series_id_rejects_garbage(bad: str) -> None:
    with pytest.raises(FredValidationError) as exc:
        GetSeriesInput(series_id=bad)
    assert exc.value.field == "series_id"


def test_get_series_empty_id_rejected() -> None:
    # Empty string trips the min_length StringConstraint (Pydantic),
    # not our before-validator.
    with pytest.raises((FredValidationError, ValidationError)):
        GetSeriesInput(series_id="")


@pytest.mark.parametrize("bad_date", ["2024-13-01", "2024/01/01", "not-a-date", "20240101"])
def test_get_series_bad_start_rejected(bad_date: str) -> None:
    with pytest.raises(FredValidationError) as exc:
        GetSeriesInput(series_id="GDP", start=bad_date)
    assert exc.value.field == "start"


def test_get_series_bad_end_rejected() -> None:
    with pytest.raises(FredValidationError) as exc:
        GetSeriesInput(series_id="GDP", end="2024-13-99")
    assert exc.value.field == "end"


def test_get_series_invalid_calendar_date() -> None:
    with pytest.raises(FredValidationError):
        GetSeriesInput(series_id="GDP", start="2024-02-30")


def test_get_series_limit_bounds() -> None:
    with pytest.raises(ValidationError):
        GetSeriesInput(series_id="GDP", limit=0)
    with pytest.raises(ValidationError):
        GetSeriesInput(series_id="GDP", limit=100001)


def test_get_series_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        GetSeriesInput(series_id="GDP", surprise="x")  # type: ignore[call-arg]


def test_get_series_non_string_id_passthrough() -> None:
    # A non-string id is left for Pydantic's type coercion / rejection.
    with pytest.raises(ValidationError):
        GetSeriesInput(series_id=123)  # type: ignore[arg-type]


def test_get_series_non_string_date_passthrough() -> None:
    with pytest.raises(ValidationError):
        GetSeriesInput(series_id="GDP", start=123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# SearchSeriesInput
# ---------------------------------------------------------------------------


def test_search_minimal() -> None:
    args = SearchSeriesInput(query="  real gdp  ")
    assert args.query == "real gdp"
    assert args.limit == 25


def test_search_limit_bounds() -> None:
    with pytest.raises(ValidationError):
        SearchSeriesInput(query="x", limit=0)
    with pytest.raises(ValidationError):
        SearchSeriesInput(query="x", limit=1001)


def test_search_query_too_long() -> None:
    with pytest.raises(ValidationError):
        SearchSeriesInput(query="a" * 201)


def test_search_non_string_query() -> None:
    with pytest.raises(ValidationError):
        SearchSeriesInput(query=5)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# GetSeriesLatestInput
# ---------------------------------------------------------------------------


def test_latest_ok() -> None:
    assert GetSeriesLatestInput(series_id="unrate").series_id == "UNRATE"


def test_latest_bad_id() -> None:
    with pytest.raises(FredValidationError):
        GetSeriesLatestInput(series_id="bad id!")


# ---------------------------------------------------------------------------
# GetReleaseCalendarInput
# ---------------------------------------------------------------------------


def test_calendar_default() -> None:
    assert GetReleaseCalendarInput().days == 14


def test_calendar_bounds() -> None:
    with pytest.raises(ValidationError):
        GetReleaseCalendarInput(days=0)
    with pytest.raises(ValidationError):
        GetReleaseCalendarInput(days=181)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_supported_tools() -> None:
    tools = supported_tool_names()
    assert tools == [
        "get_series",
        "search_series",
        "get_series_latest",
        "get_release_calendar",
        "health_check",
        "get_server_info",
    ]
    # Returns a fresh list (defensive copy).
    tools.append("x")
    assert "x" not in supported_tool_names()
