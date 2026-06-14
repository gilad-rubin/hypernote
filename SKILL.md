---
name: hypernote
description: Work against Hypernote's notebook-first SDK and agent-first CLI. Use this when an agent needs to create notebooks, insert or edit cells, run code without an open Lab tab, inspect notebook status or diffs, or verify JupyterLab attach/streaming behavior.
---

# hypernote

`hypernote` is the notebook runtime surface. The SDK is the core API. The CLI is a thin shell over that SDK.

Run `uv run hypernote` for a live workspace dashboard when you need
orientation. Use `--help` only as an explicit discovery fallback when the task
is not covered by this skill or the docs.

## Prerequisite

Hypernote CLI requires two things:

1. **`hypernote` installed in the current repo's environment.**
   If `uv run hypernote setup doctor` fails because the command is missing,
   install it first:
   ```bash
   uv add hypernote --dev
   ```

2. **A running Hypernote-enabled JupyterLab server.**
   See "Server lifecycle" below.

Once both are in place, all commands are just `uv run hypernote ...`.

## Server lifecycle

One server serves all notebooks and all agents in a workspace. Do not start multiple servers.

**Check if a server is already running:**

```bash
uv run hypernote setup doctor
```

For headless CLI/SDK work, the important fields are `hypernote_api`,
`jupyter_server_nbmodel`, `jupyter_server_ydoc`, and `jobs_endpoint`. If those
are usable and `default_kernel` is plausible for the repo, use the server.
`default_kernel` may be a kernelspec name such as `python` or an interpreter
path. `jupyter_collaboration` or `jupyter_docprovider` showing `missing` is not
by itself a blocker for headless execution; investigate those only when browser
or JupyterLab collaboration behavior is failing.

**If no server is running, start one from the repo that owns the notebook:**

```bash
uv run hypernote setup serve --no-browser > tmp/hypernote-serve.log 2>&1 &
```

`setup serve` is a foreground process. Agents that need to keep working should
background it and redirect logs. The default address is `http://127.0.0.1:8888`.
Omit `--no-browser` when the user wants JupyterLab opened as part of setup; use
`--no-browser` only for quiet headless automation or when you will open a
specific notebook URL yourself.

Servers launched by `setup serve` use temporary Jupyter collaboration journal storage.
Notebook contents and outputs still persist through the `.ipynb` file once the
shared document is saved.

**If the server is running but `default_kernel` points to the wrong Python** (e.g., a
different repo's `.venv`), stop the old server and start a new one with `setup serve`
from this repo. This is the only case where you should restart a running server.

To stop the server: press `Ctrl+C` in the terminal where `setup serve` is running, or
if it's backgrounded, find its pid with `lsof -ti :8888` and kill it.

**If port 8888 is taken**, use a different port and point all commands at it:

```bash
uv run hypernote setup serve --port 8889 --no-browser > tmp/hypernote-serve-8889.log 2>&1 &
uv run hypernote --server http://127.0.0.1:8889 setup doctor
```

## Quick Start

Pick the notebook path from the user's request. If the user did not ask for a
specific path, use a unique disposable path under `tmp/` so examples and agent
runs do not overwrite each other.

```bash
uv run hypernote                   # live workspace dashboard and hints
uv run hypernote setup doctor            # check for existing server
uv run hypernote setup serve --no-browser > tmp/hypernote-serve.log 2>&1 &  # only if the API is unreachable and quiet headless setup is desired
for _ in 1 2 3 4 5 6 7 8 9 10; do
  uv run hypernote setup doctor | grep -q '"hypernote_api"[[:space:]]*:[[:space:]]*"ok"' && break
  sleep 0.5
done
uv run hypernote setup doctor            # readiness check after a cold start
notebook_path="tmp/demo-$(date +%Y%m%d-%H%M%S).ipynb"
uv run hypernote create "$notebook_path" --empty --brief
uv run hypernote ix "$notebook_path" -s 'value = 20 + 22' --brief
uv run hypernote status "$notebook_path" --brief
```

## Fast Agent Smoke Test

When the task is simply to prove headless execution and optionally open the
result in JupyterLab, keep the workflow to the product path. Use the path the
user requested; the `tmp/` path below is only for disposable smoke tests:

```bash
uv run hypernote setup doctor
# only if the Hypernote API is unreachable and you do not want setup to open Lab:
uv run hypernote setup serve --no-browser > tmp/hypernote-serve.log 2>&1 &
for _ in 1 2 3 4 5 6 7 8 9 10; do
  uv run hypernote setup doctor | grep -q '"hypernote_api"[[:space:]]*:[[:space:]]*"ok"' && break
  sleep 0.5
done
uv run hypernote setup doctor
notebook_path="tmp/demo-$(date +%Y%m%d-%H%M%S).ipynb"
uv run hypernote create "$notebook_path" --empty --brief
uv run hypernote ix "$notebook_path" -s 'value = 20 + 22; print(value)' --brief
```

Run `setup serve` only when `setup doctor` shows the Hypernote API is
unreachable or the server belongs to the wrong environment. If you start the
server from an agent, redirect its output to `tmp/*.log`; Jupyter startup,
autosave, kernel, and 404 messages are operational noise unless the server
failed to start.

If the user asks to open JupyterLab during setup, omit `--no-browser`. If they
ask to open the resulting notebook, use the browser handoff URL below after the
headless command succeeds.

To hand the notebook to a browser or UI surface, open:

```text
<server-from-setup-doctor>/lab/tree/<notebook-path>
```

URL-encode path segments that contain spaces or special URL characters. Simple
workspace-relative paths such as `tmp/demo.ipynb` can be used directly.

For a smoke test, do not inspect broad docs, run command help, or stream server
logs unless the direct path fails and that information is needed for recovery.
Browser automation is only the final handoff step; Hypernote work should happen
through `setup serve`, `create`, and `ix`.

## Common Agent Recipes

Use these compact paths before reaching for `--help` or broad notebook reads.
For agent loops, add `--brief` to `create`, `ix`, `exec`, `status`,
`edit replace`, and focused `cat` reads when you want the cell result without
command hints, snapshot tokens, raw output payloads, or per-cell batch chatter.
`--brief` still preserves `output_preview`; use the full command or
`cat --output CELL_ID --brief --full-output` when the preview is not enough.

- **Iterative multi-line work:** `create --empty`, then pipe each substantial
  cell into `ix` with a heredoc. This includes loops, long-running cells,
  f-strings, and any source that contains nested quotes. Inspect the just-run
  cell with `cat --output CELL_ID --brief` before deciding the next `ix`. If
  the output preview is truncated or the full result matters, use
  `cat --output CELL_ID --brief --full-output`; this is still a focused cell read.
  `CELL_ID` is the id reported by the preceding `ix` result. In `--brief`
  output, use `cell_ids[0]` or `cells[].id`; in the full output, use
  `inserted_cells[].id` or `job.cell_ids`. If automating extraction, parse that
  JSON field; do not infer the id from notebook order.
- **Failed cell recovery:** read the failed cell output, replace the same cell,
  then execute it again:
  ```bash
  uv run hypernote cat nb.ipynb --output CELL_ID --brief
  uv run hypernote edit replace nb.ipynb CELL_ID -s 'print("fixed")' --brief
  uv run hypernote exec nb.ipynb CELL_ID --brief
  ```
  `edit replace` changes source without running it; old outputs may remain until
  the following `exec`. For multi-line replacements, pipe the fixed source
  through stdin instead of using `-s`.
- **Plot and rich output inspection:** run the plotting cell with `ix`, save
  the rendered images to files, then read the saved files to literally see the
  result:
  ```bash
  cat <<'EOF' | uv run hypernote ix nb.ipynb --brief
  import matplotlib.pyplot as plt
  plt.plot([1, 2, 3])
  plt.show()
  EOF
  uv run hypernote cat nb.ipynb --output CELL_ID --save-images tmp/plots --brief
  ```
  The result lists the written files under `saved_images`; open them with your
  file reader. Output previews summarize rich outputs as `data_keys` such as
  `image/png`; use `cat nb.ipynb --mime CELL_ID --brief` when you need the raw
  MIME bundle JSON instead of files (add `--full-output` for intact base64).
- **Known-good batch:** use `ix --cells-file` only when intermediate inspection
  is unnecessary. Batch output may contain one compact result for each executed
  code cell plus a final aggregate; markdown cells are represented in the final
  aggregate. The file is a JSON array of cell objects:
  ```json
  [
    {"id": "setup", "type": "code", "source": "value = 21 * 2\nprint(value)"},
    {"type": "code", "source": "print('batch-ok')"}
  ]
  ```
  Each object supports `source`, optional `type` or `cell_type` (`code` or
  `markdown`), and optional `id`.
  ```bash
  uv run hypernote ix "$notebook_path" --cells-file tmp/cells.json --brief
  ```
- **SDK happy path:** use one short Python script that connects, inserts,
  runs, waits, and prints `nb.status().summary`. Use `nb.status(full=True)` only
  when you need full source/output details. For proof of a single cell's output,
  call `nb.status().cell(cell.id).output_preview(full_output=True)`.

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

Do not use `-s` for multi-line code, loops, f-strings with nested quotes, or
other non-trivial source. Shell quoting can corrupt the cell before Hypernote
receives it.

For long-running cells, combine stdin with `--no-wait` so the command returns a
`job_id` and `cell_ids` immediately while the notebook keeps executing:

```bash
cat <<'EOF' | uv run hypernote ix nb.ipynb --no-wait --brief
import time
from datetime import datetime

for tick in range(1, 181):
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"tick {tick}/180 at {stamp}", flush=True)
    time.sleep(1)
EOF
```

Use the returned cell id for focused monitoring, for example
`uv run hypernote cat nb.ipynb --tail-output CELL_ID --brief`.

### Commands

- `ix` — insert a cell and run it. This is the primary command.
- `exec` — re-run an existing cell by id (useful after editing a failed cell)
- `edit` — mutate cell source or structure without executing
- `run-all` / `restart` / `restart-run-all` — notebook-wide execution
- `status` / `diff` / `cat` — inspect notebook state and outputs with summary-first reads, filtered cells, and focused output flags

### When a cell fails

1. Read the error output from `ix`.
2. Use `cat --cell <cell-id>` or `cat --output <cell-id>` if you need a compact view of the failure.
3. Fix the source with `edit replace PATH CELL_ID -s '...'` for short single-line
   code, or pipe new source through stdin for multi-line fixes.
4. Re-run with `exec <cell-id>`.
5. Continue with the next `ix`.

Do not re-insert a failed cell. Edit it in place and re-execute.

### `--cells-file` (batch mode)

`ix --cells-file` inserts and runs multiple cells sequentially. Use it only for known-good
cell sequences where you do not need to inspect intermediate outputs.

If batch mode halts early (cell failure, interrupt, or input prompt), the output includes
`halt_reason`, `last_processed_cell_id`, `cells_inserted`, and `cells_remaining` so you
know exactly where to resume. Cells after the halt point were never inserted into the notebook.

## Operator Practices

1. Work iteratively: `ix` one cell, read the output, then decide the next cell.
2. Use `create --empty` so you start with a clean notebook, not a Jupyter-inserted blank cell.
3. Use heredoc or `--source-file` for multi-line cells. Never pass multi-line code through `-s`.
4. Prefer the SDK and CLI over raw HTTP for notebook operation.
5. Treat Jupyter shared documents as the source of truth. Open or closed JupyterLab tabs should show the same notebook result.
6. For agents, prefer `--brief` output unless you intentionally need the fuller JSON payload.
7. Start with `hypernote` itself when you need workspace context and the next best action.
8. Use `--stream-json` only when you plan to watch the process; otherwise it wastes context.
9. Start the server with `hypernote setup serve` instead of hand-writing Jupyter flags.
10. Skip large rich outputs such as `graph.visualize()` in agent automation unless the visualization is the point of the run. When it is the point, use `cat --save-images DIR` and read the saved image files instead of pulling base64 into context.
11. Use unique notebook paths in smoke tests and demos.
12. Move durable notes into `docs/` or `dev/`; keep `tmp/` disposable.
13. Treat Hypernote jobs, runtime state, and cell attribution as ephemeral coordination state, not durable history.
