---
name: hypernote
description: Work against Hypernote's notebook-first SDK and agent-first CLI. Use this when an agent needs to create notebooks, insert or edit cells, run code headlessly, inspect notebook status or diffs, or verify JupyterLab attach/streaming behavior.
---

# hypernote

`hypernote` is the notebook runtime surface. The SDK is the core API. The CLI is a thin shell over that SDK.

Design rule: if a compact read model, truncation rule, or focused observation flow is useful in the CLI and also generally useful to agents or other adapters, define it in the SDK first and let the CLI render it, rather than re-encoding the logic in the CLI.

Review rule: prefer finishing a feature with contract cleanup, not just feature coverage. Variants should share one envelope, aggregate names should match their exact semantics, and adapters should normalize boundary payload shapes instead of assuming one representation.

Run `uv run hypernote` for a live workspace dashboard, `uv run hypernote --help` for the current command list, and `uv run hypernote <command> --help` for exact syntax.

## Prerequisite

Hypernote CLI requires two things:

1. **`hypernote` installed in the current repo's environment.**
   If `uv run hypernote --help` fails, install it first:
   ```bash
   uv add hypernote --dev
   ```

2. **A running Jupyter server with the Hypernote extension enabled.**
   See "Server lifecycle" below.

Once both are in place, all commands are just `uv run hypernote ...`.

## Server lifecycle

One server serves all notebooks and all agents in a workspace. Do not start multiple servers.

**Check if a server is already running:**

```bash
uv run hypernote setup doctor
```

The output includes `default_kernel` — the Python interpreter the server's kernel uses.
Verify it points to your repo's `.venv/bin/python`. If it does, the server is good — use it.

**If no server is running, start one in the background:**

```bash
uv run hypernote setup serve &
```

`setup serve` is a foreground process — run it in the background so your terminal stays
available. The default address is `http://127.0.0.1:8888`.

**If the server is running but `default_kernel` points to the wrong Python** (e.g., a
different repo's `.venv`), stop the old server and start a new one with `setup serve`
from this repo. This is the only case where you should restart a running server.

To stop the server: press `Ctrl+C` in the terminal where `setup serve` is running, or
if it's backgrounded, find its pid with `lsof -ti :8888` and kill it.

**If port 8888 is taken**, use a different port and point all commands at it:

```bash
uv run hypernote setup serve --port 8889 &
uv run hypernote --server http://127.0.0.1:8889 setup doctor
```

## Quick Start

```bash
uv run hypernote                   # live workspace dashboard and hints
uv run hypernote setup doctor            # check for existing server
uv run hypernote setup serve &           # only if no server is running
uv run hypernote create tmp/demo.ipynb --empty
uv run hypernote ix tmp/demo.ipynb -s 'value = 20 + 22'
uv run hypernote status tmp/demo.ipynb --full
```

## Core Workflow

The normal way to build and run a notebook is one cell at a time:

```text
ix  →  read output  →  decide next cell  →  ix  →  repeat
```

This is the happy path. Insert a cell, run it, observe the output, then decide what comes next.
Do not pre-plan all cells and batch-insert them. Work iteratively.

### Passing cell source

Three input modes, from simplest to most robust:

1. **Inline** for short single-line cells:
   ```bash
   uv run hypernote ix nb.ipynb -s 'print("hello")'
   ```

2. **Stdin with heredoc** for multi-line cells (preferred for anything non-trivial):
   ```bash
   cat <<'EOF' | uv run hypernote ix nb.ipynb
   import pandas as pd
   df = pd.read_csv("data.csv")
   print(df.head())
   EOF
   ```

3. **Source file** when the cell is very large or already on disk:
   ```bash
   uv run hypernote ix nb.ipynb --source-file path/to/cell.py
   ```

Do not use `-s` for multi-line code. Shell quoting will corrupt newlines.

### Commands

- `ix` — insert a cell and run it. This is the primary command.
- `exec` — re-run an existing cell by id (useful after editing a failed cell)
- `edit` — mutate cell source or structure without executing
- `run-all` / `restart` / `restart-run-all` — notebook-wide execution
- `status` / `diff` / `cat` — inspect notebook state and outputs with summary-first reads, filtered cells, and focused output flags

### When a cell fails

1. Read the error output from `ix`.
2. Use `cat --cell <cell-id>` or `cat --output <cell-id>` if you need a compact view of the failure.
3. Fix the source with `edit replace`.
4. Re-run with `exec <cell-id>`.
5. Continue with the next `ix`.

Do not re-insert a failed cell. Edit it in place and re-execute.

### `--cells-file` (batch mode)

`ix --cells-file` inserts and runs multiple cells sequentially. Use it only for known-good
cell sequences where you do not need to inspect intermediate outputs.

If batch mode halts early (cell failure, interrupt, or input prompt), the output includes
`halt_reason`, `last_processed_cell_id`, `cells_inserted`, and `cells_remaining` so you
know exactly where to resume. Cells after the halt point were never inserted into the notebook.

## Best Practices

1. Work iteratively: `ix` one cell, read the output, then decide the next cell.
2. Use `create --empty` so you start with a clean notebook, not a Jupyter-inserted blank cell.
3. Use heredoc or `--source-file` for multi-line cells. Never pass multi-line code through `-s`.
4. Prefer the SDK and CLI over raw HTTP unless you are explicitly working on server routes.
5. Treat Jupyter shared documents as the source of truth. Open or closed JupyterLab tabs must not change correctness.
6. For agents, prefer default non-TTY JSON output unless you intentionally want background streaming.
7. Start with `hypernote` itself when you need workspace context and the next best action.
8. Use `--stream-json` only when you plan to watch the process; otherwise it wastes context.
9. Start the server with `hypernote setup serve` instead of hand-writing Jupyter flags.
10. Skip large rich outputs such as `graph.visualize()` in headless automation unless the visualization is the point of the run.
11. Use unique notebook paths in tests and demos.
12. Move durable notes into `docs/` or `dev/`; keep `tmp/` disposable.
13. Treat Hypernote jobs, runtime state, and cell attribution as ephemeral coordination state, not durable history.
14. When changing read/inspection behavior, update the SDK observation helpers before or alongside the CLI so every adapter shares the same summary/truncation rules.
15. Keep command hints grounded in shipped commands and actual runtime values. Do not document or suggest a focused read flag unless it exists in the CLI.
16. For contract-heavy changes, test the focused variants, empty/failure states, and alternate valid payload shapes, not just the happy path.
17. When a helper moves into the SDK or another shared layer, remove the old CLI/test copy in the same change.

## Before You Change Behavior

1. Read [AGENTS.md](AGENTS.md).
2. Check the current public surface in [docs/cli.md](docs/cli.md) and [docs/sdk.md](docs/sdk.md).
3. If browser-visible execution behavior changes, check [docs/browser-regression-spec.md](docs/browser-regression-spec.md).
4. If the VS Code embedding experience changes, check [docs/vscode-extension.md](docs/vscode-extension.md).

## Verification

Install the right tier first:

```bash
uv sync --extra dev
```

Use `uv sync` for base runtime work and `uv sync --extra lab` when you specifically need the full collaborative JupyterLab bundle without the rest of the dev toolchain.

Then run:

```bash
uv run ruff check hypernote tests
uv run python -m pytest -q
```
