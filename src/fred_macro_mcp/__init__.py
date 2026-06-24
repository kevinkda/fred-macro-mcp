"""FRED Macro Read-only MCP Server.

A Model Context Protocol (MCP) server exposing 6 tools that wrap the
FRED (Federal Reserve Economic Data, St. Louis Fed) public API
(4 business + 2 meta tools).

Public modules:
    - :mod:`fred_macro_mcp.server` — FastMCP entry point.
    - :mod:`fred_macro_mcp.client` — async httpx client wrapper + rate limit.
    - :mod:`fred_macro_mcp.cache` — pluggable response cache.
    - :mod:`fred_macro_mcp.errors` — structured exception hierarchy + redaction.
    - :mod:`fred_macro_mcp.models` — Pydantic v2 input schemas.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
