"""Tools package for fred-macro-mcp.

Business tools:
    * :mod:`series`   — ``get_series``, ``get_series_latest``.
    * :mod:`search`   — ``search_series``.
    * :mod:`releases` — ``get_release_calendar``.

Meta tools:
    * :mod:`meta`     — ``health_check``, ``get_server_info``.
"""

from __future__ import annotations

__all__ = ["meta", "releases", "search", "series"]
