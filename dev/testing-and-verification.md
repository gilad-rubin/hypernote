# Testing and Verification

## Install tiers for verification

- `uv sync`
  - base SDK/server/shared-doc runtime only
- `uv sync --extra lab`
  - base install plus the collaborative JupyterLab bundle
- `uv sync --extra dev`
  - local development and CI install; includes lint, pytest, Playwright, and browser-test prerequisites

## Fast checks

```bash
uv run ruff check hypernote tests
uv run python -m pytest -q
```

## Test layers

- unit and contract tests
  - SDK shape
  - CLI output contract
  - runtime and attribution helpers
- local dev / CI assumption
  - `uv sync --extra dev`
- live integration tests
  - server-backed notebook creation, execution, persistence, and diff behavior
- browser regression tests
  - open-tab cell appearance
  - streaming output while running
  - late-open visibility of already-produced output
  - assume the `dev` install tier

## Rules

- use unique notebook paths per test
- browser tests should also use unique JupyterLab workspace URLs
- assert semantic progress, not fragile timestamps
- when browser-visible execution behavior changes, verify both:
  - job state
  - rendered notebook state
