# CLI Reference

Hypernote's CLI is agent-first:

- bare `hypernote` shows a live workspace dashboard with next-step hints
- TTY: concise human-readable progress
- non-TTY: compact final JSON by default
- explicit streaming only with `--watch` or `--stream-json`

The CLI is a thin client. It requires a running Hypernote-enabled JupyterLab server.
Its summary-first read surfaces are backed by SDK observation helpers, so CLI
output and SDK previews use the same truncation and focused-read behavior.

Commands often append contextual hints. In human mode they appear as `hint:` lines; in JSON mode they are included in a `hints` array.

## Home view

- `hypernote`
  - live workspace state for the current directory
  - server reachability, active jobs, notebooks found under the workspace, and next-step commands
  - use this first when you need orientation without reading help text

## Core commands

- `create PATH`
  - create or connect to a notebook
- `ix PATH ...`
  - insert new cell content and run it
- `exec PATH CELL_ID [CELL_ID ...]`
  - run existing cells only
- `edit ...`
  - insert, replace, move, delete, or clear outputs
- `run-all PATH`
  - run all code cells in notebook order
- `restart PATH`
  - restart the notebook runtime
- `restart-run-all PATH`
  - restart, then run all code cells
- `interrupt PATH`
  - interrupt active execution
- `status PATH`
  - summary-first notebook state with aggregates, filtered cell lists, and recovery hints
  - use `--failed`, `--query TEXT`, `--full`, `--max-output N`, and `--full-output`
- `diff PATH --snapshot TOKEN`
  - changes since a snapshot
- `cat PATH`
  - compact cell inventory and focused reads
  - use `--cell CELL_ID`, `--output CELL_ID`, `--tail-output CELL_ID`, `--no-outputs`, `--full`, `--max-output N`, and `--full-output`
  - use `--mime CELL_ID` for raw output MIME bundles (base64 payloads truncated at `--max-output` unless `--full-output`)
  - use `--save-images DIR` to write image outputs (png, jpeg, svg) to files and report the saved paths; combine with `--cell`, `--output`, or `--mime` to save one cell, or use alone to save every cell's images

## Secondary commands

- `job get`
- `job await`
- `job stdin`
- `runtime status`
- `runtime ensure`
- `runtime stop`
- `setup serve`
  - start a Hypernote-enabled JupyterLab server in the current Python environment
- `setup doctor`
  - use `setup doctor --path PATH` to compare notebook metadata against the live runtime and surface kernel mismatches
  - for headless CLI/SDK work, `hypernote_api`, `jupyter_server_nbmodel`,
    `jupyter_server_ydoc`, and `jobs_endpoint` are the key reachability fields;
    missing collaboration/docprovider frontend packages are browser/JupyterLab
    concerns, not automatic blockers for headless execution

Job and runtime commands are for the live notebook session. They should be treated as ephemeral control-plane interfaces, not durable history queries.

## Output modes

- default TTY: attached human-readable progress
- default non-TTY: one compact JSON result for most single-action commands
- `ix --cells-file` may emit one compact JSON result per executed code cell plus
  a final aggregate; treat it as multi-object stdout, not a single JSON document
- notebook read/write commands expose `--json`, `--pretty`, and `--human`
- execution wait commands also expose `--watch`, `--stream-json`, and `--progress=quiet|events|full`
- operator commands such as `setup doctor` have their own smaller option sets
- supported notebook commands expose `--brief` for low-noise agent JSON:
  `create`, `ix`, `exec`, `status`, `cat`, and `edit replace`

## Exit codes

Commands that wait on a job (`run-all`, `restart-run-all`, `exec`, `ix`, and
`job await`) exit nonzero when the run does not succeed, so scripts can branch
on `$?` without parsing JSON:

- `0` — the job(s) succeeded, or ended `awaiting_input` (a recoverable pause).
- `1` — the job ended `failed` or `interrupted`. The JSON result (including a
  batch `ix` partial-state summary with `halt_reason`) is still written to
  stdout first; only the process exit code reflects the failure.

This includes the `exec --no-wait` then `job await` pattern: `exec --no-wait`
returns before the outcome is known so it always exits `0`, and the later
`job await` is where the failure surfaces in the exit code. (`run-all` and
`restart-run-all` have no `--no-wait`.) `job get` only reads the current status
and never exits nonzero for a failed job. Non-execution commands (`status`,
`cat`, `diff`, `edit`, etc.) exit nonzero only on their own errors (bad
arguments, missing notebook, server failure), never because a previously-run
cell has error output.

## Brief Mode

Use `--brief` when an agent needs the result of the command without extra
context. Brief mode preserves cell `output_preview` values, but omits hints,
snapshot tokens, full notebook cell lists, raw output payloads, and per-code-cell
batch chatter. Use the normal command shape, `--full-output`, or
`cat --output CELL_ID --brief --full-output` when the preview is too small.
Focused `cat --output CELL_ID --brief` preserves output line breaks in the JSON
`text` field. Brief `ix`/`exec` output previews are compact one-line summaries.
Focused `cat --mime CELL_ID --brief` and `cat ... --save-images DIR --brief`
keep `mime_bundles` and `saved_images` while dropping the summary and hints.

## Read Patterns

- prefer `status` when you want notebook health, counts, runtime state, or a filtered subset of cells
- prefer `cat` when you want to inspect cells, outputs, or a single cell by id
- use `--full` only when you need the full source and output text; otherwise leave truncation in place
- use `--failed` and `--query` to narrow `status` results instead of parsing the full notebook
- use `--output` or `--tail-output` when you only need the latest output text from one cell
- for substantial cell results, use `cat --output CELL_ID --brief --full-output`
  so you see the full result without reading the whole notebook
- when an output's `data_keys` mention `image/png`, `image/jpeg`, or
  `image/svg+xml`, use `cat --output CELL_ID --save-images DIR` and read the
  saved files to literally see the plot, or `cat --mime CELL_ID` for the raw
  MIME bundle JSON
- compact JSON reads may include snapshot tokens and recovery hints for follow-up commands unless `--brief` is used

## Command intent

- prefer `hypernote` with no subcommand to orient yourself before taking action
- prefer `setup doctor` before starting or replacing a server
- prefer `setup serve` as the shortest local bootstrap path when no usable
  Hypernote API is reachable
- prefer `ix` for new work
- prefer `exec` for rerunning known cells
- prefer `status` and `diff` over ad hoc polling
- prefer `cat` for focused inspection and `status` for notebook health
- prefer `--brief` output for agent loops unless you need the fuller JSON payload
- prefer `setup doctor` before assuming a server or extension problem
- after a failed batch `ix`, prefer `cat --output CELL_ID` or `status --failed` and `exec` over rerunning the whole batch blindly
- keep `--help` as the explicit discovery path when the hints are not enough

## Serve bootstrap

- `hypernote setup serve`
  - starts a Hypernote-enabled JupyterLab server in the current Python environment
  - opens a browser tab by default; use `--no-browser` to keep the same server without opening a tab
  - uses Jupyter's temporary real-time collaboration journal; the `.ipynb` remains the durable notebook artifact
- agent automation that does not want an automatic Lab tab should redirect
  long-running server output, for example
  `uv run hypernote setup serve --no-browser > tmp/hypernote-serve.log 2>&1 &`,
  and surface only the URL or the relevant startup failure. If the user wants
  the browser opened during setup, omit `--no-browser`.
- `hypernote --server http://127.0.0.1:8899 setup serve --root /path/to/repo`
  - starts the server for another repo or port while keeping execution in that repo's environment
- if the JupyterLab integration stack is missing in the current env, `setup serve` fails with an install hint instead of a long module error

Minimal headless smoke test:

```bash
uv run hypernote setup doctor
# only if the Hypernote API is unreachable and quiet headless setup is desired:
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

Use the path the user or workflow requested. The `tmp/demo-...` path is only a
safe disposable example for tests and agent smoke checks.

To hand the notebook to a browser, open
`<server-from-setup-doctor>/lab/tree/<notebook-path>`.
URL-encode path segments that contain spaces or special URL characters. Simple
workspace-relative paths such as `tmp/demo.ipynb` can be used directly.

For non-trivial multi-line cells, prefer stdin. This avoids shell-quoting bugs
with loops, f-strings, nested quotes, and other source that is easy to corrupt
before Hypernote receives it:

```bash
cat <<'EOF' | uv run hypernote ix "$notebook_path" --brief
values = [13, 21, 34]
print(sum(values))
EOF
```

For long-running cells, add `--no-wait` so the command returns a `job_id` and
`cell_ids` immediately while the notebook keeps executing:

```bash
cat <<'EOF' | uv run hypernote ix "$notebook_path" --no-wait --brief
import time
from datetime import datetime

for tick in range(1, 181):
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"tick {tick}/180 at {stamp}", flush=True)
    time.sleep(1)
EOF
```

Use the returned cell id for focused monitoring:

```bash
uv run hypernote cat "$notebook_path" --tail-output CELL_ID --brief
```

## Rich output inspection

Plots, HTML, and images live in the notebook as MIME bundles. Text previews
summarize them as `data_keys`; the focused flags make them inspectable:

```bash
cat <<'EOF' | uv run hypernote ix "$notebook_path" --brief
import matplotlib.pyplot as plt
plt.plot([1, 2, 3])
plt.show()
EOF
uv run hypernote cat "$notebook_path" --output CELL_ID --save-images tmp/plots --brief
# Read the reported tmp/plots/CELL_ID-out0.png to see the rendered plot.
uv run hypernote cat "$notebook_path" --mime CELL_ID --brief
```

`--mime` truncates long payloads such as base64 images at `--max-output`
characters and records the original sizes under `data_truncated`; add
`--full-output` for intact payloads. `--save-images` decodes `image/png` and
`image/jpeg` to real image bytes and writes `image/svg+xml` as text, then
reports the file paths under `saved_images`.

## Batch execution notes

- `ix --cells-file` inserts and runs cells sequentially
- if a code cell fails, is interrupted, or awaits input, later cells may not exist yet
- batch `ix` summaries now report `halt_reason` and `last_processed_cell_id` when they stop early
- timeout errors include the job id, last known status, and a recovery hint pointing to `job get` and `cat`
- cells-file output can emit one result per executed code cell plus the final
  aggregate; markdown cells appear in the aggregate rather than as standalone
  execution results

The cells file must contain a JSON array. Each object supports `source`,
optional `type` or `cell_type` (`code` or `markdown`), and optional `id`:

```json
[
  {"id": "intro", "type": "markdown", "source": "# Demo"},
  {"id": "setup", "type": "code", "source": "value = 21 * 2\nprint(value)"},
  {"type": "code", "source": "print('batch-ok')"}
]
```

Run it with:

```bash
uv run hypernote ix "$notebook_path" --cells-file tmp/cells.json --brief
```

## Recovery examples

For a failed inserted cell, keep the same cell and re-run it after replacing the
source:

```bash
uv run hypernote cat nb.ipynb --output CELL_ID --brief
uv run hypernote edit replace nb.ipynb CELL_ID -s 'print("fixed")' --brief
uv run hypernote exec nb.ipynb CELL_ID --brief
```

`edit replace` changes source without executing. Existing outputs may remain
visible until the following `exec` writes new outputs.
Use `-s` only for short single-line replacements; for multi-line fixes, pipe the
source through stdin or use `--source-file`.
Use the cell id from the preceding `ix` result, either `inserted_cells[].id` or
`job.cell_ids` in the full output, or `cell_ids[0]` / `cells[].id` in
`--brief` output.

Stream outputs are rendered from Jupyter output payloads and normalized to text.
