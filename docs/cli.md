# CLI Reference

Hypernote's CLI is agent-first:

- TTY: concise human-readable progress
- non-TTY: compact final JSON by default
- explicit streaming only with `--watch` or `--stream-json`

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
- `setup doctor`

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

- prefer `ix` for new work
- prefer `exec` for rerunning known cells
- prefer `status` and `diff` over ad hoc polling
- prefer default non-TTY output for agents unless you truly need background observation
