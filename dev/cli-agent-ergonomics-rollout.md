# CLI Agent Ergonomics Rollout

This document tracks the AXI-inspired improvements for Hypernote's CLI.

Status:

- Phase 1 is shipped and documented in the public CLI reference.
- Phase 2 and Phase 3 remain open.

Goals:

- reduce agent discovery turns
- keep default non-TTY reads compact
- make notebook state easier to act on without extra calls
- preserve JSON compatibility and the existing SDK-first CLI contract

Non-goals for now:

- replacing JSON with a custom format
- shell hook ambient context
- changing all error output conventions at once

## Phase 1 Shipped

- [x] Add contextual `hint:` lines after high-value commands.
- [x] Make bare `hypernote` show a live home view instead of help text.
- [x] Add precomputed notebook aggregates to `status`.
- [x] Redesign default `cat` output to be summary-first.
- [x] Truncate large outputs by default in agent-oriented reads.
- [x] Add focused read flags for notebook inspection.
- [x] Make empty states explicit across notebook, runtime, and job reads.

Shipped behavior:

- `hypernote` with no subcommand returns actionable live state instead of Click help
- `status` answers "is this notebook healthy?" from top-level fields
- `cat` answers "what cells are here and which one should I inspect?" without full dumps
- large outputs are truncated in non-TTY reads unless `--full-output` is used
- hints only reference shipped commands and real runtime values
- the compact read model is now SDK-backed so the CLI is not the only place that knows the summary/truncation rules

## Remaining Backlog

### Phase 2

- [ ] Add a one-command repair path for failed cells.
- [ ] Add richer recovery hints for failed, timed out, and awaiting-input jobs.
- [ ] Make `job get` and `runtime status` summary-first.
- [ ] Make `setup doctor` more decision-oriented.
- [ ] Flatten agent-oriented JSON output where it helps avoid deep nesting.

### Phase 3

- [ ] Add `--fields` support on read commands.
- [ ] Normalize `--full`, `--full-output`, and `--max-output` semantics.
- [ ] Add stable machine-readable error codes for JSON mode.
- [ ] Shorten subcommand help and make it example-led.
- [ ] Add concrete command examples to help output.
- [ ] Keep responses content-first and help-second.
- [ ] Include notebook-relative context in hints where possible.
- [ ] Consider a dedicated `home` or `dashboard` command if the bare root becomes overloaded.
- [ ] Consider optional ambient context hooks after core command outputs are strong.
- [ ] Add richer notebook health summaries for stale or mismatched runtime states.

## Deferred

- TOON or other custom output formats
- shell/session hook ambient context
- sweeping stdout/stderr contract changes
