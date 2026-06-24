# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-06-25

Initial release — a read-only MCP server for FRED (Federal Reserve Economic
Data, St. Louis Fed) macroeconomic time series.

### Added

- **6 read-only tools**:
  - `get_series` — observation values for one series over an optional
    `start`/`end` window (ISO dates), with missing points surfaced as `null`.
  - `search_series` — keyword search across the FRED catalog, ordered by
    popularity, to discover the right `series_id`.
  - `get_series_latest` — the single most-recent observation for a series.
  - `get_release_calendar` — upcoming FRED data releases in the next *N* days
    (macro event-risk overlay).
  - `health_check` — local probe (API key configured? rate limit?); never
    calls FRED.
  - `get_server_info` — version, MCP SDK version, tool list; never calls FRED.
- **Async httpx client** with a sliding-60-second token bucket honoring
  FRED's documented 120 req/min ceiling, exponential back-off + jitter on
  5xx, and `Retry-After`-aware 429 handling.
- **Structured error hierarchy** (`FredError` and subclasses) with
  `redact_secrets()` masking the FRED API key (`api_key=…` query params and
  bare 32-char keys) plus operator emails from every rendered message.
- **Pydantic v2 input schemas** with anchored `series_id` / ISO-date
  validation — inputs are bound query parameters, never concatenated into a
  URL.
- **Pluggable cache backend** (v0.7 T0 template): in-process memory LRU + TTL
  by default (zero external dependency, off by default), with an opt-in
  ClickHouse backend (`pip install fred-macro-mcp[clickhouse]`) and graceful
  `requires_clickhouse_persistence` degradation.
- **Security posture**: SSRF-safe fixed host (`https://api.stlouisfed.org`),
  no redirect following, TLS always verified, API key never logged.
- **100% test coverage** (line + branch) via `respx` mocks — no real FRED
  API calls in the suite. Includes OWASP Top 10 (2017/2021/2025), penetration,
  exception, boundary, unit, and integration tests, plus an API-key redaction
  audit.
- **Tooling**: reusable CI (`kevinkda/mcp-ci-templates`), CodeQL, Dependabot,
  pre-commit (ruff / mypy / detect-secrets / gitleaks), clean pinned
  dependencies to clear known pip-audit CVEs.

[0.1.0]: https://github.com/kevinkda/fred-macro-mcp/releases/tag/v0.1.0
