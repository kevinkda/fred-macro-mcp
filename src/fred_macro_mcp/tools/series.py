"""``get_series`` and ``get_series_latest`` implementations.

FRED endpoint used:
    * ``GET /fred/series/observations?series_id=...`` — observation values
      for one economic data series.

The series id and the optional date window are passed as **bound query
parameters** (never string-concatenated into the path), and the request
host is fixed in :mod:`client`, so neither injection nor SSRF is possible.

Reference: https://fred.stlouisfed.org/docs/api/fred/series_observations.html
"""

from __future__ import annotations

from typing import Any

from ..cache import Cache
from ..client import FredClient
from ..models import GetSeriesInput, GetSeriesLatestInput
from ._runtime import call_with_cache

_OBS_PATH = "/fred/series/observations"


def _coerce_value(raw: Any) -> float | None:
    """Convert a FRED observation value to ``float`` or ``None``.

    FRED uses the literal ``"."`` for a missing observation; numeric values
    arrive as strings.  Anything unparseable degrades to ``None`` (never
    raises) so a single bad point cannot break a whole series.
    """
    if not isinstance(raw, str) or raw in {"", "."}:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _zip_observations(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Project FRED's observation rows into a compact ``{date, value}`` list."""
    obs = data.get("observations")
    if not isinstance(obs, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in obs:
        if not isinstance(entry, dict):
            continue
        date = entry.get("date")
        if not isinstance(date, str):
            continue
        out.append({"date": date, "value": _coerce_value(entry.get("value"))})
    return out


async def get_series_impl(args: GetSeriesInput) -> dict[str, Any]:
    """Return the observation series for ``args.series_id``.

    Optionally bounded by ``args.start`` / ``args.end`` (ISO dates) and
    capped at ``args.limit`` observations.
    """

    async def fetch(client: FredClient) -> dict[str, Any]:
        params: dict[str, Any] = {
            "series_id": args.series_id,
            "observation_start": args.start,
            "observation_end": args.end,
            "limit": args.limit,
            "sort_order": "asc",
        }
        data = await client.get_json(_OBS_PATH, params=params)
        observations = _zip_observations(data)
        return {
            "series_id": args.series_id,
            "start": args.start,
            "end": args.end,
            "units": data.get("units"),
            "observation_count": len(observations),
            "observations": observations,
        }

    def _lookup(cache: Cache) -> dict[str, Any] | None:
        return cache.get_series_obs(_cache_params(args))

    def _store(cache: Cache, raw: dict[str, Any]) -> None:
        cache.put_series_obs(_cache_params(args), raw)

    return await call_with_cache(fetch, cache_lookup=_lookup, cache_store=_store)


async def get_series_latest_impl(args: GetSeriesLatestInput) -> dict[str, Any]:
    """Return the single most-recent observation for ``args.series_id``.

    Uses ``sort_order=desc`` + ``limit=1`` so FRED returns only the latest
    point — cheaper than pulling the whole history when an agent just wants
    "what is CPI right now".
    """

    async def fetch(client: FredClient) -> dict[str, Any]:
        params: dict[str, Any] = {
            "series_id": args.series_id,
            "sort_order": "desc",
            "limit": 1,
        }
        data = await client.get_json(_OBS_PATH, params=params)
        observations = _zip_observations(data)
        latest = observations[0] if observations else None
        return {
            "series_id": args.series_id,
            "latest": latest,
            "units": data.get("units"),
        }

    def _lookup(cache: Cache) -> dict[str, Any] | None:
        return cache.get_series_obs(_cache_params_latest(args))

    def _store(cache: Cache, raw: dict[str, Any]) -> None:
        cache.put_series_obs(_cache_params_latest(args), raw)

    return await call_with_cache(fetch, cache_lookup=_lookup, cache_store=_store)


def _cache_params(args: GetSeriesInput) -> dict[str, Any]:
    return {
        "tool": "get_series",
        "series_id": args.series_id,
        "start": args.start,
        "end": args.end,
        "limit": args.limit,
    }


def _cache_params_latest(args: GetSeriesLatestInput) -> dict[str, Any]:
    return {
        "tool": "get_series_latest",
        "series_id": args.series_id,
    }


__all__ = ["get_series_impl", "get_series_latest_impl"]
