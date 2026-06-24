# Security

`fred-macro-mcp` is a read-only MCP server that issues plain HTTPS `GET`
requests against the single FRED host `https://api.stlouisfed.org`. It has
**no OAuth, no bearer/refresh token, no order-placement path, and no
customer-account data**.

For the full STRIDE catalogue and trust-boundary detail, see
[`docs/THREAT_MODEL.md`](./THREAT_MODEL.md). This document is the short
operator-facing summary.

## Threat model (summary)

The one secret is the FRED API key. The concerns are:

- **`FRED_API_KEY` leakage** — the key authenticates against the operator's
  FRED quota. It is read from the environment, sent to FRED only as a bound
  query parameter, and **redacted** from every log line and exception message
  (`redact_secrets()` masks `api_key=…` query params and bare 32-char keys).
- **SSRF** — the request host is a hard-coded constant; callers pass an
  endpoint path + params dict only and cannot redirect to another host.
  Redirects are not followed.
- **Injection** — `series_id` and dates are validated with anchored regexes
  and passed as bound query parameters, never string-concatenated into a URL.
- **Fair-use rate abuse** — exceeding FRED's 120 req/min budget can get the
  key throttled. A sliding-60-second token bucket enforces the budget.
- **TLS spoofing / MITM** — httpx `verify=True` always; never disabled.

## Secret handling

- The only secret is `FRED_API_KEY`, sourced from `.env` (git-ignored) or
  the host's `env` block. It is never logged at any level.
- Pre-commit runs `detect-secrets`; CI runs `gitleaks` on every push and PR.

## Read/write boundary

This MCP is **read-only by design**: it performs HTTPS GET requests only
against FRED; there is no write / mutation path of any kind.

## Reporting security issues

Open a private security advisory on GitHub:
<https://github.com/kevinkda/fred-macro-mcp/security/advisories>.
Do **not** open a public issue with the details.
