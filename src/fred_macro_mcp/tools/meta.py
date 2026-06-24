"""Meta tools: ``health_check`` and ``get_server_info``.

These are local-only — they never touch FRED so they remain available even
when ``FRED_API_KEY`` is unset.  ``health_check`` reports whether the key is
configured (format-only; it never sends the key anywhere) so an agent can
diagnose a misconfiguration before a tool call fails.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import mcp

from ..cache import cache_enabled, get_cache
from ..client import (
    ENV_API_KEY,
    FRED_HARD_RATE_LIMIT_PER_MIN,
    resolve_rate_limit,
)
from ..errors import FredConfigurationError
from ..models import supported_tool_names

# Captured at import time so health_check stays offline-safe.
_SERVER_VERSION: str | None = None


def _safe_api_key_status() -> dict[str, Any]:
    """Check API-key presence/format without raising into the caller.

    Never returns the key value — only a boolean + a redacted reason.
    """
    raw = os.environ.get(ENV_API_KEY, "").strip()
    if not raw:
        return {"configured": False, "reason": "missing"}
    try:
        from ..client import resolve_api_key

        resolve_api_key()
    except FredConfigurationError as exc:
        return {"configured": False, "reason": exc.hint}
    return {"configured": True, "reason": None}


def _safe_cache_summary() -> dict[str, Any]:
    if not cache_enabled():
        return {"enabled": False, "backend": None, "entries": 0}
    cache = get_cache()
    if cache is None:
        return {"enabled": False, "backend": None, "entries": 0}
    try:
        stats = cache.get_stats()
    except Exception:
        return {"enabled": True, "backend": None, "entries": 0}
    return {
        "enabled": stats.enabled,
        "backend": stats.backend,
        "entries": stats.entries,
    }


async def health_check_impl() -> dict[str, Any]:
    """Local health probe.  Never calls FRED.

    ``overall_status`` is ``unhealthy`` when the API key is unconfigured
    (the server cannot call FRED), else ``ok``.
    """
    api_key = _safe_api_key_status()
    cache_summary = _safe_cache_summary()

    overall_status = "ok" if api_key["configured"] else "unhealthy"

    return {
        "server_version": _SERVER_VERSION,
        "api_key_configured": api_key["configured"],
        "api_key_reason": api_key["reason"],
        "rate_limit_per_min": resolve_rate_limit() if api_key["configured"] else None,
        "rate_limit_hard_cap": FRED_HARD_RATE_LIMIT_PER_MIN,
        "cache_enabled": cache_summary["enabled"],
        "cache_backend": cache_summary["backend"],
        "cache_entries": cache_summary["entries"],
        "platform_supported": True,
        "overall_status": overall_status,
    }


async def get_server_info_impl(*, server_version: str) -> dict[str, Any]:
    """Local server metadata — version + tool list.  Never calls FRED."""
    global _SERVER_VERSION
    _SERVER_VERSION = server_version
    return {
        "server_version": server_version,
        "mcp_sdk_version": getattr(mcp, "__version__", "unknown"),
        "python_version": (f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"),
        "supported_tools": supported_tool_names(),
        "platform_supported_v1": ["macos>=11", "linux"],
    }


__all__ = ["get_server_info_impl", "health_check_impl"]
