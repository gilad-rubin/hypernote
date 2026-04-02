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

### `Job`

- `id`
- `status`
- `cell_ids`
- `wait()`
- `refresh()`
- `send_stdin(...)`

`Job` is a live coordination handle backed by Hypernote's in-memory ledger. It is intended for current execution flow and recent status, not long-term history.

## Observation model

- `nb.status()` returns current notebook state
- `nb.status(full=True)` includes full source and outputs
- `snap = nb.snapshot()` captures a diff baseline
- `nb.diff(snapshot=snap)` returns changed cells only

## Public errors

- `HypernoteError`
- `NotebookNotFoundError`
- `CellNotFoundError`
- `RuntimeUnavailableError`
- `ExecutionTimeoutError`
- `InputNotExpectedError`
