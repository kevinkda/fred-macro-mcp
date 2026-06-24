"""FastMCP server entry point — 6 outward-facing tools.

The first thing this module does is harden stdio so no stray ``print`` /
log line pollutes the JSON-RPC stream:

* monkey-patch ``builtins.print`` so the default ``file`` is ``sys.stderr``;
* install a :class:`RotatingFileHandler` writing to
  ``${XDG_STATE_HOME}/fred-macro-mcp/logs/server.log``;
* force ``httpx`` / ``httpcore`` to ``WARNING``.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 0) Stdio hardening — must run BEFORE we import anything that might log /
#    print at import time (httpx, etc).
# ---------------------------------------------------------------------------
import builtins
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


def _harden_stdio() -> None:
    """Install the print + logging mitigations."""
    # 1) builtins.print → stderr by default.
    _orig_print = builtins.print

    def _safe_print(*args: Any, file: Any = None, **kwargs: Any) -> None:
        _orig_print(*args, file=file or sys.stderr, **kwargs)

    builtins.print = _safe_print

    # 2) Logging - RotatingFileHandler + StreamHandler(stderr).
    from . import _platform

    log_dir: Path | None = _platform.state_root() / "fred-macro-mcp" / "logs"
    try:
        assert log_dir is not None
        with _platform.restrictive_umask():
            log_dir.mkdir(parents=True, exist_ok=True)
        if not _platform.IS_WINDOWS:  # pragma: no branch - POSIX-only chmod; Windows side N/A in CI
            _platform.secure_chmod(log_dir, 0o700)
    except OSError:
        log_dir = None

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_dir is not None:
        try:
            file_handler = RotatingFileHandler(
                log_dir / "server.log",
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            file_handler.setFormatter(
                logging.Formatter('{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":%(message)r}')
            )
            handlers.append(file_handler)
        except OSError:
            pass

    level = os.environ.get("LOG_LEVEL", "WARNING").upper()
    logging.basicConfig(
        handlers=handlers,
        level=getattr(logging, level, logging.WARNING),
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":%(message)r}',
        force=True,
    )
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_harden_stdio()


# ---------------------------------------------------------------------------
# 0b) Load .env from the current working directory.  Host-injected env vars
#     win because ``override=False``.
# ---------------------------------------------------------------------------
def _bootstrap_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except ImportError:  # pragma: no cover
        pass


_bootstrap_dotenv()


# ---------------------------------------------------------------------------
# Imports after hardening
# ---------------------------------------------------------------------------

from typing import Final  # noqa: E402

from mcp.server.fastmcp import FastMCP  # noqa: E402

from . import __version__ as SERVER_VERSION  # noqa: E402
from .errors import (  # noqa: E402
    FredApiError,
    FredConfigurationError,
    FredError,
    FredNotFoundError,
    FredRateLimitError,
    FredTransientError,
    FredValidationError,
)
from .models import (  # noqa: E402
    GetReleaseCalendarInput,
    GetSeriesInput,
    GetSeriesLatestInput,
    SearchSeriesInput,
)
from .tools import meta, releases, search, series  # noqa: E402

log = logging.getLogger("fred_macro_mcp.server")

SERVER_NAME: Final[str] = "fred-macro-mcp"


# ---------------------------------------------------------------------------
# Error framing — convert structured exceptions to JSON-friendly dicts so
# the MCP client surfaces actionable messages instead of stack traces.
# ---------------------------------------------------------------------------


def _frame_error(exc: BaseException) -> dict[str, Any]:
    """Convert any exception into a structured error envelope."""
    if isinstance(exc, FredValidationError):
        return {"error": "validation", "field": exc.field, "reason": exc.reason}
    if isinstance(exc, FredConfigurationError):
        return {"error": "configuration", "hint": exc.hint}
    if isinstance(exc, FredNotFoundError):
        return {"error": "not_found", "resource": exc.resource, "hint": exc.hint}
    if isinstance(exc, FredRateLimitError):
        return {
            "error": "rate_limit",
            "retry_after_seconds": exc.retry_after_seconds,
            "current_window_used": exc.current_window_used,
        }
    if isinstance(exc, FredApiError):
        return {"error": "api_error", "status_code": exc.status_code, "hint": exc.hint}
    if isinstance(exc, FredTransientError):
        return {
            "error": "transient",
            "status_code": exc.status_code,
            "attempt": exc.attempt,
            "hint": exc.hint,
        }
    if isinstance(exc, FredError):
        return {"error": "fred_error", "type": type(exc).__name__}
    return {"error": "internal", "type": type(exc).__name__}


# ---------------------------------------------------------------------------
# FastMCP wiring
# ---------------------------------------------------------------------------


def _build_mcp() -> FastMCP:
    mcp_app = FastMCP(SERVER_NAME)

    # FastMCP ctor does not expose a ``version=`` kwarg, so the underlying
    # lowlevel ``Server.version`` defaults to ``None`` and the ``initialize``
    # response falls back to the framework version.  Inject the project
    # release tag so ``serverInfo.version`` reflects this package's
    # ``__version__``.
    mcp_app._mcp_server.version = SERVER_VERSION

    @mcp_app.tool()
    async def get_series(
        series_id: str,
        start: str | None = None,
        end: str | None = None,
        limit: int = 100000,
    ) -> dict[str, Any]:
        """Return the observation series for a FRED economic data series.

        ``series_id`` is a FRED identifier (e.g. ``GDP``, ``CPIAUCSL``,
        ``UNRATE``, ``DGS10``).  ``start`` / ``end`` optionally bound the
        window as ISO ``YYYY-MM-DD`` dates.
        """
        try:
            args = GetSeriesInput(series_id=series_id, start=start, end=end, limit=limit)
            return await series.get_series_impl(args)
        except FredError as exc:
            return _frame_error(exc)

    @mcp_app.tool()
    async def search_series(query: str, limit: int = 25) -> dict[str, Any]:
        """Search the FRED catalog for series matching *query* keywords.

        Returns the most popular matches with their frequency, units, and
        observation range so an agent can pick the right ``series_id``.
        """
        try:
            args = SearchSeriesInput(query=query, limit=limit)
            return await search.search_series_impl(args)
        except FredError as exc:
            return _frame_error(exc)

    @mcp_app.tool()
    async def get_series_latest(series_id: str) -> dict[str, Any]:
        """Return the single most-recent observation for a FRED series.

        Cheaper than ``get_series`` when an agent only needs the current
        value of a macro indicator.
        """
        try:
            args = GetSeriesLatestInput(series_id=series_id)
            return await series.get_series_latest_impl(args)
        except FredError as exc:
            return _frame_error(exc)

    @mcp_app.tool()
    async def get_release_calendar(days: int = 14) -> dict[str, Any]:
        """Return upcoming FRED data releases in the next *days* days.

        Useful for macro overlay: knowing when the next CPI / GDP / jobs
        print lands lets an agent flag event risk on the calendar.
        """
        try:
            args = GetReleaseCalendarInput(days=days)
            return await releases.get_release_calendar_impl(args)
        except FredError as exc:
            return _frame_error(exc)

    @mcp_app.tool()
    async def health_check() -> dict[str, Any]:
        """Local health probe.  Never calls FRED."""
        return await meta.health_check_impl()

    @mcp_app.tool()
    async def get_server_info() -> dict[str, Any]:
        """Local server metadata.  Never calls FRED."""
        return await meta.get_server_info_impl(server_version=SERVER_VERSION)

    return mcp_app


# Lazy build so test collection (which imports server) doesn't fail when
# stdio is already connected to pytest's capture.
_app: FastMCP | None = None


def app() -> FastMCP:
    global _app
    if _app is None:
        _app = _build_mcp()
    return _app


def main() -> None:
    """Console-script entry point."""
    log.info('{"event":"server_start","version":"%s"}', SERVER_VERSION)
    app().run()


__all__ = [
    "SERVER_NAME",
    "SERVER_VERSION",
    "app",
    "main",
]
