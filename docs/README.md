# Hypernote Docs

Hypernote is a notebook-first execution layer on top of Jupyter shared documents.

Jupyter owns durable notebook contents and outputs. Hypernote owns an ephemeral control plane for runtimes, jobs, and attribution.

Operator docs:

- [Getting Started](getting-started.md)
- [CLI Reference](cli.md)
- [SDK Reference](sdk.md)

Operator-only agents can stop after these three pages for normal notebook
creation, execution, recovery, SDK use, and browser handoff.

Advanced and contributor docs:

- [Runtime Model](runtime-model.md)
- [Browser Regression Spec](browser-regression-spec.md)

Runtime Model and Browser Regression Spec are for architecture questions and
browser-visible behavior changes.

The common operator check is `uv run hypernote setup doctor`. If no Hypernote API is
reachable, bootstrap the local server with `uv run hypernote setup serve`. Use
`--no-browser` and redirect the long-running server logs to `tmp/` for quiet
automation; omit `--no-browser` when the user wants setup to open JupyterLab.
For agent command loops, use `--brief` on supported commands to keep cell output
previews while dropping hints, snapshots, and bulky raw payloads.
The bare `hypernote` command is the live workspace dashboard: it shows current
workspace state, active jobs, and next-step hints without needing a subcommand.

Public docs should describe shipped behavior only. Internal implementation notes belong in `dev/`.
Public docs should describe stable contracts and user-visible invariants, not just happy-path examples.
