# Hypernote

- **JupyterLab-first** - Hypernote is a thin execution control plane for a Hypernote-enabled JupyterLab server.
- **One notebook truth** - notebook edits, execution, and late-open JupyterLab views all operate on the same logical document.
- **Agent-first surface** - the Python SDK is primary, and the CLI is a thin shell over it.
- **Ephemeral control plane** - Jupyter owns durable `.ipynb` contents and outputs; Hypernote owns in-memory runtimes, jobs, and attribution.
- **Temporary collaboration journal** - `setup serve` keeps Jupyter RTC updates in server-local temp storage, not repo-root databases.

## What it ships

- notebook-first SDK in `src/hypernote/sdk.py`
- agent-first CLI in `src/hypernote/cli/main.py`
- Jupyter server extension for execution and runtime control
- subshell-routed execute, interrupt, and restart so JupyterLab stays usable while Hypernote is running cells
- notebook-scoped runtime lifecycle with attach, detach, recovery, and stop
- job polling and `input()` round-trips for agent automation without requiring an open Lab tab
- live-server and browser regression coverage for shared-document behavior

## Quick start

```bash
uv sync
uv run hypernote setup doctor
# If no Hypernote API is reachable, start the server from this repo.
# Omit --no-browser when you want setup to open JupyterLab immediately.
uv run hypernote setup serve --no-browser > tmp/hypernote-serve.log 2>&1 &
for _ in 1 2 3 4 5 6 7 8 9 10; do
  uv run hypernote setup doctor | grep -q '"hypernote_api"[[:space:]]*:[[:space:]]*"ok"' && break
  sleep 0.5
done
uv run hypernote setup doctor
notebook_path="tmp/demo-$(date +%Y%m%d-%H%M%S).ipynb"
uv run hypernote create "$notebook_path" --empty --brief
uv run hypernote ix "$notebook_path" -s 'value = 20 + 22; print(value)' --brief
uv run hypernote status "$notebook_path" --brief
```

Use the notebook path you actually want. The `tmp/demo-...` path above is only a
disposable example that avoids overwriting an existing notebook.
`--brief` keeps the cell's `output_preview` while omitting hints, snapshot
tokens, and bulky raw output payloads.

For another repo's environment, install Hypernote there (`uv add hypernote --dev`) and run
`setup doctor` / `setup serve` from that repo so kernels and notebook paths line up.

## Install

The default install includes the JupyterLab integration stack Hypernote needs:
JupyterLab, shared-document support, server-side notebook execution, and the
Hypernote server extension.

Use `hypernote[dev]` only for local development and CI tooling.

Examples:

```bash
uv sync
uv sync --extra dev
```

## Mental model

Jupyter owns:

- notebook persistence
- shared YDoc document state
- temporary collaboration journal state for live RTC updates
- kernel and session primitives
- notebook rendering in JupyterLab

Hypernote owns:

- runtime lifecycle around a notebook
- job coordination and stdin round-trips
- actor attribution
- SDK, CLI, and thin REST handlers

## Contributor discipline

- shared behavior should have one owner, usually the SDK for agent-facing observation rules
- command and payload variants should preserve one contract unless a difference is explicit and documented
- adapters should normalize valid upstream shape differences at the boundary
- tests should cover invariants across variants, not only the main workflow

## Documentation

Operator docs:

- [Getting Started](docs/getting-started.md)
- [CLI Reference](docs/cli.md)
- [SDK Reference](docs/sdk.md)

Contributor and advanced behavior docs:

- [Agent Contributor Guide](dev/agent-contributor.md)
- [Runtime Model](docs/runtime-model.md)
- [Browser Regression Spec](docs/browser-regression-spec.md)

Agents that only need to create, run, recover, or open notebooks can stop after
the operator docs.

## Verification

For local development and CI, install the dev tier first:

```bash
uv sync --extra dev
```

```bash
uv run ruff check src/hypernote tests
uv run python -m pytest -q
```
