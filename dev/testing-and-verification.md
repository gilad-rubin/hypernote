# Testing and Verification

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
- live integration tests
  - server-backed notebook creation, execution, persistence, and diff behavior
- browser regression tests
  - open-tab cell appearance
  - streaming output while running
  - late-open visibility of already-produced output

## Rules

- use unique notebook paths per test
- browser tests should also use unique JupyterLab workspace URLs
- assert semantic progress, not fragile timestamps
- when browser-visible execution behavior changes, verify both:
  - job state
  - rendered notebook state
