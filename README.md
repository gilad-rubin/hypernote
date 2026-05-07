# Hypernote

- **Notebook-first** - Hypernote is a thin execution control plane on top of Jupyter shared documents.
- **One notebook truth** - notebook edits, execution, and late-open JupyterLab views all operate on the same logical document.
- **Agent-first surface** - the Python SDK is primary, and the CLI is a thin shell over it.
- **Ephemeral control plane** - Jupyter owns durable `.ipynb` contents and outputs; Hypernote owns in-memory runtimes, jobs, and attribution.

## What it ships

- notebook-first SDK in `src/hypernote/sdk.py`
- agent-first CLI in `src/hypernote/cli/main.py`
- Jupyter server extension for execution and runtime control
- subshell-routed execute, interrupt, and restart so JupyterLab stays usable while Hypernote is running cells
- notebook-scoped runtime lifecycle with attach, detach, recovery, and stop
- job polling and `input()` round-trips for headless execution
- live-server and browser regression coverage for shared-document behavior

## Quick start

```bash
uv sync
uv run hypernote --help
uv run hypernote setup serve
uv run hypernote setup doctor
uv run hypernote create tmp/demo.ipynb
uv run hypernote ix tmp/demo.ipynb -s 'value = 20 + 22\nprint(value)'
uv run hypernote status tmp/demo.ipynb --full
```

For another repo's environment, install Hypernote there (`uv add hypernote --dev`) and run
the same bootstrap command from that repo.

## Install tiers

- `hypernote`
  - core + server + shared-doc runtime
  - use this for real Hypernote SDK/server usage
- `hypernote[lab]`
  - adds `jupyter-collaboration` for multi-user shared-document support
  - use this when you want JupyterLab's collaborative editing on top of the base runtime
- `hypernote[dev]`
  - adds test, lint, browser, and local dev tooling
  - use this for local development and CI

Examples:

```bash
uv sync
uv sync --extra lab
uv sync --extra dev
```

## Mental model

Jupyter owns:

- notebook persistence
- shared YDoc document state
- kernel and session primitives
- notebook rendering in JupyterLab

Hypernote owns:

- runtime lifecycle around a notebook
- job coordination and stdin round-trips
- actor attribution
- SDK, CLI, and thin REST handlers

## Design discipline

- shared behavior should have one owner, usually the SDK for agent-facing observation rules
- command and payload variants should preserve one contract unless a difference is explicit and documented
- adapters should normalize valid upstream shape differences at the boundary
- tests should cover invariants across variants, not only the main workflow

## Documentation

- [Getting Started](docs/getting-started.md)
- [CLI Reference](docs/cli.md)
- [SDK Reference](docs/sdk.md)
- [Runtime Model](docs/runtime-model.md)
- [Browser Regression Spec](docs/browser-regression-spec.md)

## Verification

For local development and CI, install the dev tier first:

```bash
uv sync --extra dev
```

```bash
uv run ruff check src/hypernote tests
uv run python -m pytest -q
```
