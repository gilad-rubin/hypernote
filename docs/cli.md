# CLI Reference

Hypernote's CLI is agent-first:

- bare `hypernote` shows a live workspace dashboard with next-step hints
- TTY: concise human-readable progress
- non-TTY: compact final JSON by default
- explicit streaming only with `--watch` or `--stream-json`

The CLI is a thin client. It requires a running Hypernote-enabled Jupyter server.
Its summary-first read surfaces are backed by SDK observation helpers so the CLI, agents, and
other adapters can share the same truncation and focused-read rules.

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
  - use `setup doctor --path PATH` to compare notebook metadata against the live runtime and surface kernel mismatches

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

## Read Patterns

- prefer `status` when you want notebook health, counts, runtime state, or a filtered subset of cells
- prefer `cat` when you want to inspect cells, outputs, or a single cell by id
- use `--full` only when you need the full source and output text; otherwise leave truncation in place
- use `--failed` and `--query` to narrow `status` results instead of parsing the full notebook
- use `--output` or `--tail-output` when you only need the latest output text from one cell

## Command intent

- prefer `hypernote` with no subcommand to orient yourself before taking action
- prefer `setup serve` as the shortest local bootstrap path
- prefer `ix` for new work
- prefer `exec` for rerunning known cells
- prefer `status` and `diff` over ad hoc polling
- prefer `cat` for focused inspection and `status` for notebook health
- prefer default non-TTY output for agents unless you truly need background observation
- prefer `setup doctor` before assuming a server or extension problem
- after a failed batch `ix`, prefer `cat --cell CELL_ID --output` or `cat --failed` and `exec` over rerunning the whole batch blindly
- keep `--help` as the explicit discovery path when the hints are not enough

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
