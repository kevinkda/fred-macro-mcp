# Threat model — `fred-macro-mcp`

STRIDE analysis for a read-only MCP server wrapping the FRED public API.

## Assets

| Asset | Sensitivity |
| ----- | ----------- |
| `FRED_API_KEY` | High — authenticates the operator's FRED quota. |
| FRED response data | Public (FRED is open data). |
| Local cache (if enabled) | Low — public data, in-process memory by default. |

## Trust boundaries

1. **MCP host → server** (stdio JSON-RPC) — tool arguments are untrusted and
   validated by Pydantic schemas with anchored regexes.
2. **Server → FRED** (HTTPS) — single fixed host; TLS verified.

## STRIDE

| Threat | Vector | Mitigation |
| ------ | ------ | ---------- |
| **S**poofing | MITM of FRED host | TLS `verify=True` (never disabled); fixed host constant; no redirect following. |
| **T**ampering | Malicious `series_id` injected into URL | `series_id`/dates validated with anchored regexes; passed as **bound query parameters**, never concatenated into the path. |
| **R**epudiation | No audit trail | Structured JSON logs to a rotating file (stderr + `${XDG_STATE_HOME}/fred-macro-mcp/logs`). |
| **I**nformation disclosure | API key in logs / errors | `redact_secrets()` masks `api_key=…` and bare 32-char keys in every exception; client logs paths only, never full URLs with the key. |
| **D**enial of service | Rate-limit ban | Sliding-60s token bucket ≤ 120 req/min (FRED's ceiling); 429 honors `Retry-After`. |
| **E**levation of privilege | Write/mutation abuse | None possible — server is GET-only, no FRED write API exists or is wired. |

## SSRF analysis

The base host (`https://api.stlouisfed.org`) is a module-level constant on
the httpx client (`base_url`). Tool implementations pass a fixed endpoint
*path* (e.g. `/fred/series/observations`) and a parameters dict. No user
input ever determines the host or scheme, and `follow_redirects=False`
removes the open-redirect pivot. SSRF is therefore not reachable.

## Injection analysis

FRED is queried only via httpx query parameters (`params=...`), which httpx
URL-encodes. The `series_id` is additionally constrained to
`^[A-Z0-9][A-Z0-9._&\-]{0,63}$` and dates to ISO `YYYY-MM-DD` (validated by
`date.fromisoformat`). There is no SQL, shell, or template surface in the
request path.

## Residual risk

- A compromised host could read `FRED_API_KEY` from the process environment.
  This is inherent to any API-key client and is out of scope; rotate the key
  if the host is compromised.
- FRED's data accuracy / revisions are upstream concerns, not security ones.
