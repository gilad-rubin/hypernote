---
name: hypernote
description: Work against Hypernote's notebook-first SDK and agent-first CLI. Use this when an agent needs to create notebooks, insert or edit cells, run code headlessly, inspect notebook status or diffs, or verify JupyterLab attach/streaming behavior.
---

# hypernote

`hypernote` is the notebook runtime surface. The SDK is the core API. The CLI is a thin shell over that SDK.

Run `uv run hypernote --help` for the current command list, and `uv run hypernote <command> --help` for exact syntax.

## Quick Start

```bash
uv run hypernote create tmp/demo.ipynb
uv run hypernote ix tmp/demo.ipynb -s 'value = 20 + 22\nprint(value)'
uv run hypernote status tmp/demo.ipynb --full
```

## Use This Surface

- `ix` — insert a new cell and run it; this is the default happy path
- `exec` — run existing cell ids only
- `edit` — mutate notebook cells without executing
- `run-all` / `restart` / `restart-run-all` — notebook-wide control
- `status` / `diff` — observe current notebook state and changes
- `cat` — inspect cells and outputs directly

## Best Practices

1. Prefer `ix` over separate insert + execute steps.
2. Prefer the SDK and CLI over raw HTTP unless you are explicitly working on server routes.
3. Treat Jupyter shared documents as the source of truth. Open or closed JupyterLab tabs must not change correctness.
4. For agents, prefer default non-TTY JSON output unless you intentionally want background streaming.
5. Use `--stream-json` only when you plan to watch the process; otherwise it wastes context.
6. Use unique notebook paths in tests and demos.
7. Move durable notes into `docs/` or `dev/`; keep `tmp/` disposable.

## Before You Change Behavior

1. Read [AGENTS.md](/Users/giladrubin/python_workspace/hypernote/AGENTS.md).
2. Check the current public surface in [docs/cli.md](/Users/giladrubin/python_workspace/hypernote/docs/cli.md) and [docs/sdk.md](/Users/giladrubin/python_workspace/hypernote/docs/sdk.md).
3. If browser-visible execution behavior changes, check [docs/browser-regression-spec.md](/Users/giladrubin/python_workspace/hypernote/docs/browser-regression-spec.md).

## Verification

```bash
uv run ruff check hypernote tests
uv run python -m pytest -q
```
