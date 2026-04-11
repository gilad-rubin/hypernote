# SDK Reference

The SDK is notebook-first.

## Entry point

```python
import hypernote

nb = hypernote.connect("tmp/demo.ipynb", create=True)
```

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

## Observation model

- `nb.status()` returns current notebook state
- `nb.status(full=True)` includes full source and outputs
- `snap = nb.snapshot()` captures a diff baseline
- `nb.diff(snapshot=snap)` returns changed cells only

### Summary-first helpers

`NotebookStatus` and `CellStatus` now expose compact observation helpers so the CLI and SDK
consumers can share the same summary/truncation rules instead of re-encoding them in each adapter.

- `status.aggregates()`
  - top-level counts and runtime state for the notebook
- `status.compact_cells(...)`
  - filtered, compact cell summaries with source/output previews
- `status.compact_dict(...)`
  - notebook aggregates plus compact cells in one payload
- `status.cell(cell_id)`
  - look up a specific `CellStatus` by id
- `cell.compact_dict(...)`
  - compact source/output view for one cell
- `cell.output_payload(...)`
  - focused output-only view for one cell, with optional tail preview

These helpers are intended for agent-oriented observation and tooling layers. They preserve
the notebook-first object model while centralizing truncation, error detection, and focused-read
rules in the SDK.

Contract guidance:

- shared observation semantics should live here once, then be rendered by the CLI and tests
- aggregate fields should report exact semantics, not approximations
- boundary payload normalization belongs at adapter edges, not scattered across consumers

## Public errors

- `HypernoteError`
- `NotebookNotFoundError`
- `CellNotFoundError`
- `RuntimeUnavailableError`
- `ExecutionTimeoutError`
- `InputNotExpectedError`
