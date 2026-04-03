# CLI Reference

Hypernote's CLI is agent-first:

- TTY: concise human-readable progress
- non-TTY: compact final JSON by default
- explicit streaming only with `--watch` or `--stream-json`

The CLI is a thin client. It requires a running Hypernote-enabled Jupyter server.

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
  - current notebook state
- `diff PATH --snapshot TOKEN`
  - changes since a snapshot
- `cat PATH`
  - inspect cells and outputs

## Secondary commands

- `job get`
- `job await`
- `job stdin`
- `runtime status`
- `runtime ensure`
- `runtime stop`
- `setup serve`
  - start a Hypernote-enabled Jupyter server in the current Python environment
- `setup doctor`
  - use `setup doctor --path PATH` to compare notebook metadata against the live runtime

Job and runtime commands are for the live notebook session. They should be treated as ephemeral control-plane interfaces, not durable history queries.

## Output modes

- default TTY: attached human-readable progress
- default non-TTY: one final compact JSON object
- `--json`: force final JSON
- `--pretty`: pretty-print JSON
- `--human`: force human-readable output
- `--watch`: attached human-readable progress
- `--stream-json`: JSONL event stream
- `--progress=quiet|events|full`: streaming verbosity

## Command intent

- prefer `setup serve` as the shortest local bootstrap path
- prefer `ix` for new work
- prefer `exec` for rerunning known cells
- prefer `status` and `diff` over ad hoc polling
- prefer default non-TTY output for agents unless you truly need background observation
- prefer `setup doctor` before assuming a server or extension problem
- after a failed batch `ix`, prefer `cat --no-outputs` and `exec` over rerunning the whole batch blindly

## Serve bootstrap

- `hypernote setup serve`
  - starts JupyterLab in the current Python environment with Hypernote, nbmodel, and ydoc enabled
- `hypernote --server http://127.0.0.1:8899 setup serve --root /path/to/repo`
  - starts the server for another repo or port while keeping execution in that repo's environment
- if `jupyterlab` is missing in the current env, `setup serve` fails with an install hint instead of a long module error

## Batch execution notes

- `ix --cells-file` inserts and runs cells sequentially
- if a code cell fails, is interrupted, or awaits input, later cells may not exist yet
- batch `ix` summaries now report `halt_reason` and `last_processed_cell_id` when they stop early
- timeout errors include the job id, last known status, and a recovery hint pointing to `job get` and `cat`
