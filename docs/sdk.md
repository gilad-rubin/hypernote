# SDK Reference

The SDK is notebook-first.

## Entry point

Connect to an existing or pre-created notebook:

```python
import hypernote

nb = hypernote.connect("path/to/notebook.ipynb")
```

Use the notebook path your workflow needs. Temporary `tmp/*.ipynb` paths are
useful for examples and tests, but they are not required.

To create a notebook through the SDK as a convenience, pass `create=True`:

```python
nb = hypernote.connect("path/to/notebook.ipynb", create=True)
```

Depending on the live notebook model, `create=True` may add a default blank code
cell. If exact empty state matters, create the notebook with
`uv run hypernote create PATH --empty --brief`, then connect with
`hypernote.connect(PATH)`.

## Main objects

### `Notebook`

- `path`
- `cells`
- `runtime`
- `run(*cell_ids)`
- `run_all()`
- `restart()`
- `interrupt()`
- `snapshot()`
- `status(full=False)`
- `diff(snapshot=..., full=False)`

### `CellCollection`

- `nb.cells["id"]`
- iteration
- `insert_code(...)`
- `insert_markdown(...)`

### `CellHandle`

- `id`
- `type`
- `source`
- `outputs`
- `execution_count`
- `replace(...)`
- `move(...)`
- `delete()`
- `clear_outputs()`
- `run()`

### `Runtime`

- `status`
- `recoverable`
- `session_id`
- `kernel_id`
- `kernel_name`
- `ensure()`
- `stop()`

`Runtime` reflects live notebook-scoped control-plane state. It is not durable across runtime stop or server restart.
`Runtime.ensure()` reopens against the notebook's current metadata kernelspec unless the caller
explicitly requests another kernel.

### `Job`

- `id`
- `status`
- `cell_ids`
- `wait()`
- `refresh()`
- `send_stdin(...)`

`Job` is a live coordination handle backed by Hypernote's in-memory ledger. It is intended for current execution flow and recent status, not long-term history.
`Job.wait(timeout=...)` raises `ExecutionTimeoutError` with the job id, last known status, and
CLI recovery hints for `job get` and `cat`.

For agent scripts, prefer a bounded wait:

```python
job.wait(timeout=60)
```

## Observation model

- `nb.status()` returns current notebook state
- `nb.status(full=True)` includes full source and outputs
- `snap = nb.snapshot()` captures a diff baseline
- `nb.diff(snapshot=snap)` returns changed cells only

For the smallest human confirmation after a run, prefer `nb.status().summary`.
Use `nb.status(full=True)` only when you need full source/output details.
`aggregates()` and `compact_dict()` include the snapshot token used by `diff`;
that is useful for tooling but noisier than the summary string.
Preview helpers return structured dictionaries for tools, not raw stdout
strings; for example, `cell.output_preview(full_output=True)` includes fields
such as `text`, `truncated`, and character counts.
You do not need `nb.status(full=True)` before calling
`status.cell(cell_id).output_preview(full_output=True)`; `full_output=True` on
the preview helper is the knob that controls the output preview.
The CLI's `--brief` mode uses the same preview helpers so agents still see the
cell result while avoiding bulky raw outputs and hints.

### Summary-first helpers

`NotebookStatus` and `CellStatus` now expose compact observation helpers so the CLI and SDK
consumers can share the same summary/truncation rules instead of re-encoding them in each adapter.

- `status.aggregates()`
  - top-level counts and runtime state for the notebook
- `status.compact_cells(full_source=False, include_outputs=False, full_output=False, failed_only=False, query=None, max_output_chars=400)`
  - filtered, compact cell summaries with source/output previews
- `status.compact_dict(full_source=False, include_outputs=False, full_output=False, failed_only=False, query=None, max_output_chars=400, include_details=False)`
  - notebook aggregates plus compact cells in one payload
- `status.cell(cell_id)`
  - look up a specific `CellStatus` by id
- `cell.has_error_output()`
  - whether the cell's outputs include an error
- `cell.source_preview(full=False, limit=...)`
  - truncated or full source preview for one cell
- `cell.output_preview(max_chars=400, full_output=False)`
  - truncated or full output preview for one cell
- `cell.compact_dict(full_source=False, include_outputs=False, full_output=False, max_output_chars=400)`
  - compact source/output view for one cell
- `cell.output_payload(max_chars=400, full_output=False, tail=False)`
  - focused output-only view for one cell, with optional tail preview

These helpers are intended for agent-oriented observation and tooling layers. They preserve
the notebook-first object model while centralizing truncation, error detection, and focused-read
rules in the SDK.

## Public errors

- `HypernoteError`
- `NotebookNotFoundError`
- `CellNotFoundError`
- `RuntimeUnavailableError`
- `ExecutionTimeoutError`
- `InputNotExpectedError`
