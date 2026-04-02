# Hypernote

- **Notebook-first** - Hypernote is a thin execution control plane on top of Jupyter shared documents.
- **One notebook truth** - notebook edits, execution, and late-open JupyterLab views all operate on the same logical document.
- **Agent-first surface** - the Python SDK is primary, and the CLI is a thin shell over it.
- **Ephemeral control plane** - Jupyter owns durable `.ipynb` contents and outputs; Hypernote owns in-memory runtimes, jobs, and attribution.

## What it ships

- notebook-first SDK in `hypernote/sdk.py`
- agent-first CLI in `hypernote/cli/main.py`
- Jupyter server extension for execution and runtime control
- notebook-scoped runtime lifecycle with attach, detach, recovery, and stop
- job polling and `input()` round-trips for headless execution
- live-server and browser regression coverage for shared-document behavior

## Quick start

```bash
uv sync
uv run hypernote --help
uv run hypernote create tmp/demo.ipynb
uv run hypernote ix tmp/demo.ipynb -s 'value = 20 + 22\nprint(value)'
uv run hypernote status tmp/demo.ipynb --full
```

## Install tiers

- `hypernote`
  - core + server + shared-doc runtime
  - use this for real Hypernote SDK/server usage
- `hypernote[lab]`
  - adds the JupyterLab collaboration bundle
  - use this when you want the full collaborative JupyterLab experience
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

## Documentation

- [Getting Started](/Users/giladrubin/python_workspace/hypernote/docs/getting-started.md)
- [CLI Reference](/Users/giladrubin/python_workspace/hypernote/docs/cli.md)
- [SDK Reference](/Users/giladrubin/python_workspace/hypernote/docs/sdk.md)
- [Runtime Model](/Users/giladrubin/python_workspace/hypernote/docs/runtime-model.md)

## Verification

For local development and CI, install the dev tier first:

```bash
uv sync --extra dev
```

```bash
uv run ruff check hypernote tests
uv run python -m pytest -q
```
