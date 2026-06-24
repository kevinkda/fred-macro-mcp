# Contributing to `fred-macro-mcp`

Thanks for taking the time to contribute. This project is small and
batch-orientated; a tight, focused PR is much easier to review than a large
omnibus.

## Bootstrap

```bash
git clone https://github.com/kevinkda/fred-macro-mcp.git
cd fred-macro-mcp

uv sync --extra dev
uv run pre-commit install
```

Copy `.env.example` to `.env` and set `FRED_API_KEY` so the integration
tests can spot-check live FRED behavior if you opt in.

## Workflow

1. Create a topic branch from `main`:

   ```bash
   git switch -c feature/short-description
   ```

2. Make small, logical commits. Conventional commit prefixes
   (`feat`, `fix`, `docs`, `test`, `chore`, `refactor`) are required.
3. Run the full local gate before pushing:

   ```bash
   uv run pytest --cov=src --cov-fail-under=100
   uv run ruff check src tests
   uv run ruff format --check src tests
   uv run mypy --strict src
   uv run pre-commit run --all-files
   ```

4. Open a PR using the template in `.github/PULL_REQUEST_TEMPLATE.md`.

## Code style

- Python 3.11+ with full type hints.
- 120-char line limit (handled by ruff format).
- Errors raised by the public surface MUST be subclasses of `FredError`.
- Never log the API key. The `redact_secrets()` helper protects exception
  text; log calls should still avoid `%r` on raw response objects.
- New tools must include:
  - A Pydantic input model in `models.py` with anchored regexes.
  - Unit tests for normal / 404 / 429 / 4xx / 5xx paths.
  - A README "Tools" entry with the four-section format
    (when to use / input / output / example).

## Security

- Never commit secrets — pre-commit hooks block obvious cases via
  `detect-secrets` (always on) and `gitleaks` (manual stage).
- Never disable TLS verification.
- Do not hard-code an API key in source; it comes from `FRED_API_KEY`.

## Licensing

By submitting a PR you agree your contribution is licensed under MIT
(matching the repo `LICENSE`).
