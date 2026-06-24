# fred-macro-mcp

[![CI](https://github.com/kevinkda/fred-macro-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/kevinkda/fred-macro-mcp/actions/workflows/test.yml)
[![CodeQL](https://github.com/kevinkda/fred-macro-mcp/actions/workflows/codeql.yml/badge.svg)](https://github.com/kevinkda/fred-macro-mcp/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

A **read-only** [Model Context Protocol](https://modelcontextprotocol.io)
(MCP) server that wraps the [FRED](https://fred.stlouisfed.org/) (Federal
Reserve Economic Data, St. Louis Fed) public API.

FRED is the canonical free source for US macroeconomic time series — GDP,
CPI, unemployment, policy and market interest rates, and the Treasury yield
curve.  This server lets an LLM agent overlay macro context on equity /
fixed-income research: "what is CPI doing", "where is the 10y-2y spread",
"when is the next jobs print".

> **Read-only by design.** Every tool performs HTTPS `GET` requests against
> the single fixed host `https://api.stlouisfed.org`. There is no write,
> mutation, or order-placement path of any kind.

## Tools

| Tool | Purpose |
| ---- | ------- |
| `get_series` | Observation values for one series over an optional date window. |
| `search_series` | Keyword-search the FRED catalog to find the right `series_id`. |
| `get_series_latest` | The single most-recent observation for a series. |
| `get_release_calendar` | Upcoming FRED data releases in the next *N* days. |
| `health_check` | Local health probe (key configured? rate limit?). Never calls FRED. |
| `get_server_info` | Version, MCP SDK version, tool list. Never calls FRED. |

### `get_series`

- **When to use:** pull a macro time series (e.g. CPI, GDP, 10y Treasury).
- **Input:** `series_id` (e.g. `CPIAUCSL`), optional `start` / `end`
  (`YYYY-MM-DD`), optional `limit`.
- **Output:** `{ series_id, start, end, units, observation_count,
  observations: [{date, value}, ...] }`. Missing points (`"."` in FRED)
  surface as `value: null`.
- **Example:** `get_series(series_id="DGS10", start="2024-01-01")`.

### `search_series`

- **When to use:** you know the concept ("unemployment rate") but not the id.
- **Input:** `query` (free text), optional `limit`.
- **Output:** `{ query, result_count, results: [{id, title, frequency,
  units, observation_start, observation_end, popularity}, ...] }`, most
  popular first.
- **Example:** `search_series(query="real gdp")`.

### `get_series_latest`

- **When to use:** "what is the current value of X" without the full history.
- **Input:** `series_id`.
- **Output:** `{ series_id, latest: {date, value} | null, units }`.
- **Example:** `get_series_latest(series_id="UNRATE")`.

### `get_release_calendar`

- **When to use:** flag upcoming macro event risk (next CPI / GDP / jobs).
- **Input:** `days` (1-180, default 14).
- **Output:** `{ days, from_date, to_date, release_count, releases:
  [{release_id, release_name, date}, ...] }`.
- **Example:** `get_release_calendar(days=30)`.

## Common series ids

| Concept | `series_id` |
| ------- | ----------- |
| Real GDP | `GDPC1` |
| CPI (all urban) | `CPIAUCSL` |
| Core PCE | `PCEPILFE` |
| Unemployment rate | `UNRATE` |
| Fed funds (effective) | `DFF` |
| 10-year Treasury | `DGS10` |
| 2-year Treasury | `DGS2` |
| 10y-2y spread | `T10Y2Y` |

## Install

```bash
uv sync --extra dev
```

A [FRED API key](https://fred.stlouisfed.org/docs/api/api_key.html) (free)
is required. Copy `.env.example` to `.env` and set `FRED_API_KEY`.

## Configure your MCP host

Add to your MCP host config (e.g. Cursor `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "fred-macro": {
      "command": "uv",
      "args": ["run", "fred-macro-mcp"],
      "cwd": "/opt/workspace/code/kevinkda/fred-macro-mcp",
      "env": { "FRED_API_KEY": "<your-fred-key>" }
    }
  }
}
```

`FRED_API_KEY` may also be set in `.env` instead of inline `env`.

## Configuration

| Env var | Default | Purpose |
| ------- | ------- | ------- |
| `FRED_API_KEY` | *(required)* | 32-char FRED key. Never logged. |
| `FRED_RATE_LIMIT_PER_MIN` | `120` | Client throttle (≤ FRED's 120/min ceiling). |
| `FRED_CACHE_ENABLED` | `false` | Opt-in read-through cache. |
| `FRED_CACHE_BYPASS` | `false` | Force fresh reads while still writing. |
| `FRED_CACHE_BACKEND` | `memory` | `memory` (zero-dep) or `clickhouse` (opt-in extra). |
| `FRED_CLICKHOUSE_URL` | *(unset)* | DSN used only when backend is `clickhouse`. |
| `LOG_LEVEL` | `WARNING` | Log verbosity. |

The cache is **off by default** and uses an in-process memory LRU when
enabled — zero external dependencies. ClickHouse is an opt-in extra
(`pip install fred-macro-mcp[clickhouse]`) for durable history.

## Security

- **API key is the only secret.** It is read from the environment, passed
  to FRED only as a bound query parameter, and **redacted** from every log
  line and exception message (`api_key=…` and bare 32-char keys are masked).
- **SSRF-safe.** The host is a hard-coded constant; callers supply an
  endpoint path + params only and can never redirect to another host.
  Redirects are not followed.
- **Injection-safe.** `series_id` and dates are validated with anchored
  regexes and passed as bound query parameters — never string-concatenated
  into a URL.
- **Rate-limited.** A sliding-60-second token bucket keeps requests within
  FRED's documented 120 req/min budget.

See [`docs/SECURITY.md`](./docs/SECURITY.md) and
[`docs/THREAT_MODEL.md`](./docs/THREAT_MODEL.md).

## Development

```bash
uv run pytest --cov=src --cov-fail-under=100
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy --strict src
```

Tests use [`respx`](https://lundberg.github.io/respx/) to mock FRED — **no
real FRED API calls are made** in the test suite.

## License

MIT — see [`LICENSE`](./LICENSE).

> Data © Federal Reserve Bank of St. Louis (FRED). Subject to FRED's
> [terms of use](https://fred.stlouisfed.org/legal/). This server is for
> interactive single-user research.
