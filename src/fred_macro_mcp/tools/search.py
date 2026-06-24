"""``search_series`` implementation.

FRED endpoint used:
    * ``GET /fred/series/search?search_text=...`` — economic data series
      matching keywords.

The free-text query is passed as a bound query parameter only.

Reference: https://fred.stlouisfed.org/docs/api/fred/series_search.html
"""

from __future__ import annotations

from typing import Any

from ..cache import Cache
from ..client import FredClient
from ..models import SearchSeriesInput
from ._runtime import call_with_cache

_SEARCH_PATH = "/fred/series/search"


def _project_series(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Project FRED's verbose series records into a compact result list."""
    seriess = data.get("seriess")
    if not isinstance(seriess, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in seriess:
        if not isinstance(entry, dict):
            continue
        out.append(
            {
                "id": entry.get("id"),
                "title": entry.get("title"),
                "frequency": entry.get("frequency"),
                "units": entry.get("units"),
                "seasonal_adjustment": entry.get("seasonal_adjustment"),
                "observation_start": entry.get("observation_start"),
                "observation_end": entry.get("observation_end"),
                "popularity": entry.get("popularity"),
            }
        )
    return out


async def search_series_impl(args: SearchSeriesInput) -> dict[str, Any]:
    """Return FRED series matching ``args.query``, ordered by popularity."""

    async def fetch(client: FredClient) -> dict[str, Any]:
        params: dict[str, Any] = {
            "search_text": args.query,
            "limit": args.limit,
            "order_by": "popularity",
            "sort_order": "desc",
        }
        data = await client.get_json(_SEARCH_PATH, params=params)
        results = _project_series(data)
        return {
            "query": args.query,
            "result_count": len(results),
            "results": results,
        }

    def _lookup(cache: Cache) -> dict[str, Any] | None:
        return cache.get_series_search(_cache_params(args))

    def _store(cache: Cache, raw: dict[str, Any]) -> None:
        cache.put_series_search(_cache_params(args), raw)

    return await call_with_cache(fetch, cache_lookup=_lookup, cache_store=_store)


def _cache_params(args: SearchSeriesInput) -> dict[str, Any]:
    return {
        "tool": "search_series",
        "query": args.query,
        "limit": args.limit,
    }


__all__ = ["search_series_impl"]
