"""Async httpx wrapper for the FRED (St. Louis Fed) public API.

FRED documents a ceiling of **120 requests per minute** per API key.  We
default to that ceiling and throttle with a sliding-60-second token bucket
so bursts never exceed it.

Security-relevant behaviour:

* **API key is required** — :func:`resolve_api_key` refuses to build a
  client when ``FRED_API_KEY`` is unset / malformed.  The key is passed as
  a query parameter (FRED's only auth mechanism) but **never** logged: the
  client logs only paths, and :mod:`errors` redacts ``api_key=...`` from
  every rendered exception.
* **SSRF-safe** — the base host is a hard-coded constant
  (``https://api.stlouisfed.org``).  Callers pass an endpoint *path* and a
  parameters dict only; they can never redirect the request to another
  host.
* Errors are normalised:
    - 404 → :class:`FredNotFoundError`
    - 429 → :class:`FredRateLimitError`
    - other 4xx (400 bad arg / 403 bad key) → :class:`FredApiError`
    - 5xx / network → :class:`FredTransientError`
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from collections import deque
from typing import Any, Final

import httpx

from .errors import (
    FredApiError,
    FredConfigurationError,
    FredNotFoundError,
    FredRateLimitError,
    FredTransientError,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration knobs
# ---------------------------------------------------------------------------

#: FRED documented ceiling — never exceed regardless of operator override.
FRED_HARD_RATE_LIMIT_PER_MIN: Final[int] = 120

#: Default target rate (configurable via env).
DEFAULT_RATE_LIMIT_PER_MIN: Final[int] = 120

DEFAULT_MAX_RETRIES_429: Final[int] = 2
DEFAULT_MAX_RETRIES_5XX: Final[int] = 3
DEFAULT_BACKOFF_BASE_SEC: Final[float] = 0.5
DEFAULT_REQUEST_TIMEOUT_SEC: Final[float] = 30.0

ENV_API_KEY: Final[str] = "FRED_API_KEY"
ENV_RATE_LIMIT: Final[str] = "FRED_RATE_LIMIT_PER_MIN"

# ---------------------------------------------------------------------------
# Host (single, fixed — SSRF mitigation: never derived from user input).
# ---------------------------------------------------------------------------

FRED_HOST: Final[str] = "https://api.stlouisfed.org"

#: FRED API keys are documented as exactly 32 lowercase alphanumerics.
_API_KEY_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9]{32}$")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def resolve_api_key() -> str:
    """Return the configured FRED API key or raise :class:`FredConfigurationError`.

    The hint never echoes the (malformed) key value to avoid leaking a
    partial secret into logs.
    """
    raw = os.environ.get(ENV_API_KEY, "").strip()
    if not raw:
        raise FredConfigurationError(
            hint=(
                f"{ENV_API_KEY} is not set.  FRED requires a free API key on "
                f"every request.  Register at "
                f"https://fred.stlouisfed.org/docs/api/api_key.html and set it in your .env."
            ),
        )
    if not _API_KEY_RE.match(raw):
        raise FredConfigurationError(
            hint=(f"{ENV_API_KEY} is set but is not a valid FRED key (expected 32 lowercase alphanumeric characters)."),
        )
    return raw


def resolve_rate_limit() -> int:
    """Return the active rate limit (≤ ``FRED_HARD_RATE_LIMIT_PER_MIN``)."""
    target = _env_int(ENV_RATE_LIMIT, DEFAULT_RATE_LIMIT_PER_MIN)
    target = max(target, 1)
    if target > FRED_HARD_RATE_LIMIT_PER_MIN:
        log.warning(
            '{"event":"rate_limit_clamped","requested":%d,"max":%d}',
            target,
            FRED_HARD_RATE_LIMIT_PER_MIN,
        )
        target = FRED_HARD_RATE_LIMIT_PER_MIN
    return target


# ---------------------------------------------------------------------------
# Token-bucket rate limiter (sliding 60-second window).
# ---------------------------------------------------------------------------


class TokenBucket:
    """Sliding-60-second token bucket (FRED's per-minute budget).

    We track outbound timestamps in a deque; before each request we evict
    timestamps older than the window and, if the deque is at capacity,
    sleep until the oldest one ages out.  The slot is recorded **after** a
    request is admitted, so we never hold the bucket across retry sleeps.
    """

    WINDOW_SECONDS: Final[float] = 60.0

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity: int = capacity
        self._timestamps: deque[float] = deque()
        self._lock: asyncio.Lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                # Evict timestamps older than the window.
                while self._timestamps and (now - self._timestamps[0]) >= self.WINDOW_SECONDS:
                    self._timestamps.popleft()
                if len(self._timestamps) < self.capacity:
                    self._timestamps.append(now)
                    return
                wait = self.WINDOW_SECONDS - (now - self._timestamps[0])
                if wait <= 0:  # pragma: no cover - unreachable: eviction at >=WINDOW guarantees wait>0
                    continue
                await asyncio.sleep(wait)

    def tokens_remaining(self) -> int:
        """Best-effort current-window headroom (no eviction; for stats only)."""
        now = time.monotonic()
        live = sum(1 for ts in self._timestamps if (now - ts) < self.WINDOW_SECONDS)
        return max(0, self.capacity - live)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class FredClient:
    """Async FRED client with rate limiting and structured errors.

    One instance per process.  Construct via :func:`make_client` so the
    API-key / rate-limit / timeout knobs are pulled from env.  The API key
    is held in memory and appended to every request's query parameters; it
    is never written to a log line.
    """

    def __init__(
        self,
        *,
        api_key: str,
        rate_limit_per_min: int,
        timeout_sec: float = DEFAULT_REQUEST_TIMEOUT_SEC,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key: str = api_key
        self.bucket: TokenBucket = TokenBucket(rate_limit_per_min)
        self._client: httpx.AsyncClient = httpx.AsyncClient(
            base_url=FRED_HOST,
            timeout=timeout_sec,
            headers={
                "User-Agent": "fred-macro-mcp",
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
            },
            transport=transport,
            # Redirects are NOT followed — FRED never redirects, and refusing
            # them removes an SSRF / open-redirect vector entirely.
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> FredClient:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        del exc_type, exc, tb
        await self.aclose()

    # ------------------------------------------------------------ requests

    def _merged_params(self, params: dict[str, Any] | None) -> dict[str, Any]:
        """Build the final query params: caller params + key + file_type.

        The API key and ``file_type=json`` are always injected here; callers
        never supply them, so they cannot be tampered with or omitted.
        """
        merged: dict[str, Any] = {} if params is None else {k: v for k, v in params.items() if v is not None}
        merged["api_key"] = self._api_key
        merged["file_type"] = "json"
        return merged

    async def _request_with_retries(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Issue a single GET against *path* with retries + error mapping.

        ``path`` is a fixed endpoint path (e.g. ``/fred/series/observations``)
        relative to :data:`FRED_HOST` — never a full URL — so the request
        host is immutable.
        """
        merged = self._merged_params(params)
        last_exc: Exception | None = None
        for attempt in range(DEFAULT_MAX_RETRIES_5XX + 1):
            await self.bucket.acquire()
            try:
                resp = await self._client.get(path, params=merged)
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ReadError) as exc:
                last_exc = exc
                if attempt >= DEFAULT_MAX_RETRIES_5XX:
                    raise FredTransientError(
                        status_code=0,
                        attempt=attempt,
                        hint=f"network error: {type(exc).__name__}",
                    ) from exc
                await asyncio.sleep(_backoff_delay(attempt))
                continue

            if resp.status_code == 200:
                return resp
            if resp.status_code == 404:
                raise FredNotFoundError(
                    resource=path,
                    hint=f"FRED returned 404 for {path}",
                )
            if resp.status_code == 429:
                if attempt >= DEFAULT_MAX_RETRIES_429:
                    retry_after = _parse_retry_after(resp)
                    raise FredRateLimitError(
                        retry_after_seconds=retry_after,
                        current_window_used=self.bucket.capacity - self.bucket.tokens_remaining(),
                    )
                await asyncio.sleep(_parse_retry_after(resp))
                continue
            if 500 <= resp.status_code < 600:
                if attempt >= DEFAULT_MAX_RETRIES_5XX:
                    raise FredTransientError(
                        status_code=resp.status_code,
                        attempt=attempt,
                        hint=f"upstream {resp.status_code}",
                    )
                await asyncio.sleep(_backoff_delay(attempt))
                continue
            # Other 4xx (400 bad arg, 403 bad key) — non-retryable.  We
            # surface FRED's own error_message when present (already
            # key-free), redacted defensively.
            raise FredApiError(
                status_code=resp.status_code,
                hint=_extract_api_error(resp),
            )

        # Should be unreachable — every loop branch either returns or raises.
        raise FredTransientError(  # pragma: no cover - defensive
            status_code=0,
            attempt=DEFAULT_MAX_RETRIES_5XX,
            hint=f"retry budget exhausted: {last_exc!r}",
        )

    # ---------------------------------------------------------------- API

    async def get_json(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET *path* and parse as a JSON object."""
        resp = await self._request_with_retries(path, params=params)
        try:
            data = resp.json()
        except ValueError as exc:
            raise FredTransientError(
                status_code=resp.status_code,
                attempt=0,
                hint=f"invalid json from {path}",
            ) from exc
        if not isinstance(data, dict):
            raise FredTransientError(
                status_code=resp.status_code,
                attempt=0,
                hint=f"unexpected json shape from {path}",
            )
        return data


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _parse_retry_after(resp: httpx.Response) -> int:
    raw = resp.headers.get("Retry-After", "")
    try:
        v = int(raw)
        return max(0, min(v, 60))
    except ValueError:
        return 1


def _backoff_delay(attempt: int) -> float:
    """Exponential back-off with jitter."""
    base = DEFAULT_BACKOFF_BASE_SEC * (2**attempt)
    return float(base + random.random() * 0.25)


def _extract_api_error(resp: httpx.Response) -> str:
    """Pull FRED's ``error_message`` from a 4xx body if present.

    FRED returns ``{"error_code": 400, "error_message": "..."}`` on bad
    arguments.  The message never contains the key, but we still feed it
    through redaction in the exception ctor as defence in depth.
    """
    try:
        body = resp.json()
    except ValueError:
        return f"FRED returned {resp.status_code}"
    if isinstance(body, dict):
        msg = body.get("error_message")
        if isinstance(msg, str) and msg:
            return f"FRED {resp.status_code}: {msg}"
    return f"FRED returned {resp.status_code}"


def make_client(transport: httpx.AsyncBaseTransport | None = None) -> FredClient:
    """Build a configured :class:`FredClient` from env."""
    return FredClient(
        api_key=resolve_api_key(),
        rate_limit_per_min=resolve_rate_limit(),
        transport=transport,
    )


__all__ = [
    "DEFAULT_MAX_RETRIES_5XX",
    "DEFAULT_MAX_RETRIES_429",
    "DEFAULT_RATE_LIMIT_PER_MIN",
    "DEFAULT_REQUEST_TIMEOUT_SEC",
    "ENV_API_KEY",
    "ENV_RATE_LIMIT",
    "FRED_HARD_RATE_LIMIT_PER_MIN",
    "FRED_HOST",
    "FredClient",
    "TokenBucket",
    "make_client",
    "resolve_api_key",
    "resolve_rate_limit",
]
