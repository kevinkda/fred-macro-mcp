"""``get_release_calendar`` implementation.

FRED endpoint used:
    * ``GET /fred/releases/dates`` — release dates for all FRED economic
      data releases, newest first by default.

We request ``include_release_dates_with_no_data=true`` so upcoming (not yet
published) releases appear, then filter to a forward window of ``args.days``
days from today.  All bounds are bound query parameters; no user input
reaches the request path.

Reference: https://fred.stlouisfed.org/docs/api/fred/releases_dates.html
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from ..cache import Cache
from ..client import FredClient
from ..models import GetReleaseCalendarInput
from ._runtime import call_with_cache

_RELEASES_DATES_PATH = "/fred/releases/dates"


def _today() -> date:
    """Today's UTC date.  Isolated so tests can monkeypatch the clock."""
    return datetime.now(tz=UTC).date()


def _project_release_dates(
    data: dict[str, Any],
    *,
    today: date,
    horizon: date,
) -> list[dict[str, Any]]:
    """Keep only release dates within ``[today, horizon]`` (inclusive)."""
    rows = data.get("release_dates")
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in rows:
        if not isinstance(entry, dict):
            continue
        raw_date = entry.get("date")
        if not isinstance(raw_date, str):
            continue
        try:
            parsed = date.fromisoformat(raw_date)
        except ValueError:
            continue
        if today <= parsed <= horizon:
            out.append(
                {
                    "release_id": entry.get("release_id"),
                    "release_name": entry.get("release_name"),
                    "date": raw_date,
                }
            )
    out.sort(key=lambda r: (r["date"], r.get("release_id") or 0))
    return out


async def get_release_calendar_impl(args: GetReleaseCalendarInput) -> dict[str, Any]:
    """Return upcoming FRED data releases in the next ``args.days`` days."""

    async def fetch(client: FredClient) -> dict[str, Any]:
        today = _today()
        horizon = today + timedelta(days=args.days)
        params: dict[str, Any] = {
            "include_release_dates_with_no_data": "true",
            "sort_order": "asc",
            "order_by": "release_date",
            "realtime_start": today.isoformat(),
            "realtime_end": horizon.isoformat(),
        }
        data = await client.get_json(_RELEASES_DATES_PATH, params=params)
        releases = _project_release_dates(data, today=today, horizon=horizon)
        return {
            "days": args.days,
            "from_date": today.isoformat(),
            "to_date": horizon.isoformat(),
            "release_count": len(releases),
            "releases": releases,
        }

    def _lookup(cache: Cache) -> dict[str, Any] | None:
        return cache.get_releases(_cache_params(args))

    def _store(cache: Cache, raw: dict[str, Any]) -> None:
        cache.put_releases(_cache_params(args), raw)

    return await call_with_cache(fetch, cache_lookup=_lookup, cache_store=_store)


def _cache_params(args: GetReleaseCalendarInput) -> dict[str, Any]:
    return {
        "tool": "get_release_calendar",
        "days": args.days,
        # Bucket by calendar day so a same-day repeat hits cache but a new
        # day re-fetches the forward window.
        "as_of": _today().isoformat(),
    }


__all__ = ["get_release_calendar_impl"]
