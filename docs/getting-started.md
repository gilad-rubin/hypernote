# Getting Started

## Install

The default install includes the JupyterLab integration stack Hypernote needs:
JupyterLab, shared-document support, server-side notebook execution, and the
Hypernote server extension.

- `hypernote`
  - SDK, CLI, Hypernote JupyterLab server integration, and shared-document execution path
- `hypernote[dev]`
  - default install plus local development, test, lint, and browser-validation tooling

Examples:

```bash
uv sync
uv sync --extra dev
```

## Server prerequisite

Hypernote CLI and SDK talk to a running Hypernote-enabled JupyterLab server.
They do not start a server for normal commands.

Check first:

```bash
uv run hypernote setup doctor
```

For headless CLI/SDK work, `hypernote_api: ok`, `jupyter_server_nbmodel: ok`,
`jupyter_server_ydoc: ok`, and `jobs_endpoint: true` mean the server is usable.
`default_kernel` may be a kernelspec name such as `python` or a Python path.
`jupyter_collaboration` or `jupyter_docprovider` showing `missing` is not by
itself a blocker for headless execution.

If no Hypernote API is reachable, bootstrap the server:

```bash
# quiet automation form; omit --no-browser if you want setup to open JupyterLab
uv run hypernote setup serve --no-browser > tmp/hypernote-serve.log 2>&1 &
uv run hypernote setup doctor
```

Omit `--no-browser` when you want setup to open JupyterLab immediately. If an
agent starts a long-running server, redirect logs to `tmp/*.log` and surface only
the URL or a real startup failure.

Servers launched by `setup serve` configure Jupyter's real-time collaboration journal as
temporary server-local state. Saved notebook contents and outputs still persist
in the `.ipynb`; unsaved live collaboration changes are not recovered through a
project-local database after a server crash.

If the notebook belongs to another repo, install Hypernote there (`uv add hypernote --dev`)
and run the same bootstrap command from that repo.

## CLI happy path

Assuming `setup doctor` shows a usable server:

```bash
notebook_path="tmp/demo-$(date +%Y%m%d-%H%M%S).ipynb"
uv run hypernote create "$notebook_path" --empty --brief
uv run hypernote ix "$notebook_path" -s 'value = 20 + 22; print(value)' --brief
uv run hypernote status "$notebook_path" --brief
```

Use the path the user or workflow actually needs. The `tmp/demo-...` path is a
safe disposable default for examples, tests, and agent smoke checks.
`--brief` preserves cell `output_preview` while dropping hints, snapshot tokens,
and bulky raw payloads.

For agent smoke tests where the server should stay quiet, redirect server logs
and keep the run to setup, create, execute:

```bash
uv run hypernote setup doctor
# only if the Hypernote API is unreachable and quiet headless setup is desired:
uv run hypernote setup serve --no-browser > tmp/hypernote-serve.log 2>&1 &
uv run hypernote setup doctor
notebook_path="tmp/demo-$(date +%Y%m%d-%H%M%S).ipynb"
uv run hypernote create "$notebook_path" --empty --brief
uv run hypernote ix "$notebook_path" -s 'value = 20 + 22; print(value)' --brief
```

Then open `<server-from-setup-doctor>/lab/tree/<notebook-path>` if a browser
handoff is needed.
URL-encode path segments that contain spaces or special URL characters. Simple
workspace-relative paths such as `tmp/demo.ipynb` can be used directly.

Use:

- `ix` when you want to insert and run new code
- `exec` when you want to run existing cells by id
- `edit` when you want to change notebook structure without running it

For non-trivial multi-line cells, pipe source through stdin instead of forcing it
through `-s` shell quoting:

```bash
cat <<'EOF' | uv run hypernote ix "$notebook_path" --brief
values = [13, 21, 34]
print(sum(values))
EOF
```

Batch mode is for known-good sequences. For exploratory work, keep using the
iterative `ix -> focused output read -> ix` loop; only switch to
`ix --cells-file` after the cells no longer need intermediate decisions.

For a known-good batch after an optional setup/import cell, use this sequence:

1. `create`
2. `ix` the setup/import cell first
3. `ix --cells-file ...` for the remaining cells
4. `cat --output CELL_ID` after failures to inspect the failed output, or
   `cat --no-outputs` when you only need partial notebook structure

`ix --cells-file` inserts and executes sequentially. Known-good setup cells can
live in the cells file too; split setup into its own `ix` when you need to
inspect it before continuing. If a code cell fails or awaits input, later cells
may not exist yet.
Cells-file output can include one compact result for each executed code cell plus
the final aggregate; markdown cells appear in the aggregate. Use `--brief` when
you want one final aggregate with per-cell `output_preview` values. Use batch
mode for known-good sequences rather than exploratory work.

The cells file is a JSON array. Each object supports `source`, optional `type` or
`cell_type` (`code` or `markdown`), and optional `id`:

```json
[
  {"id": "setup", "type": "code", "source": "value = 21 * 2\nprint(value)"},
  {"type": "code", "source": "print('batch-ok')"}
]
```

Run it with:

```bash
uv run hypernote ix "$notebook_path" --cells-file tmp/cells.json --brief
```

## SDK happy path

If you want an exactly empty notebook, create it first:

```bash
uv run hypernote create path/to/notebook.ipynb --empty --brief
```

```python
import json
import hypernote

path = "path/to/notebook.ipynb"
nb = hypernote.connect(path)
cell = nb.cells.insert_code("value = 20 + 22\nprint(value)", id="hello")
job = cell.run()
job.wait(timeout=60)

print(nb.status().summary)
print(json.dumps(nb.status().cell(cell.id).output_preview(full_output=True)))
```

`connect(..., create=True)` uses Jupyter's notebook creation path, which may add
a default blank code cell. If an exact empty notebook matters, create it first
with `uv run hypernote create PATH --empty`, then connect with
`hypernote.connect(PATH)`.

## Key guarantee

Hypernote uses one logical notebook truth. A notebook may be:

- not currently open in a Lab tab
- already open in JupyterLab
- opened mid-execution

The notebook state, execution state, and outputs should still agree.

## Lifecycle expectation

- notebook contents and outputs persist in the `.ipynb` because Jupyter owns the document
- Jupyter's collaboration journal is temporary server-local state for servers launched by `setup serve`
- runtime state, jobs, and cell attribution are ephemeral Hypernote control-plane state
- stopping a runtime or restarting the server clears that control-plane state
- runtime creation resolves the requested kernel first, otherwise notebook metadata
  `kernelspec.name`, otherwise `python3`
- if notebook metadata changes after a runtime is already live, stop or restart the runtime
  to pick up the new kernelspec
