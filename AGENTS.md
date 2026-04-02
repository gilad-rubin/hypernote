# Hypernote

Notebook-first execution system built on top of Jupyter shared documents

## Quick Start

```bash
uv sync --extra dev
uv run hypernote --help
uv run hypernote create tmp/demo.ipynb
uv run hypernote ix tmp/demo.ipynb -s 'value = 20 + 22\nprint(value)'
uv run hypernote status tmp/demo.ipynb --full
uv run python -m pytest -q
uv run ruff check hypernote tests
```

## Architecture

Jupyter owns notebook truth:

- `.ipynb` persistence
- shared YDoc document state
- kernel and session primitives
- notebook rendering in JupyterLab

Hypernote owns a thin control plane:

- public SDK in [hypernote/sdk.py](/Users/giladrubin/python_workspace/hypernote/hypernote/sdk.py)
- public errors in [hypernote/errors.py](/Users/giladrubin/python_workspace/hypernote/hypernote/errors.py)
- agent-first CLI in [hypernote/cli/main.py](/Users/giladrubin/python_workspace/hypernote/hypernote/cli/main.py)
- execution orchestration and shared-document mutation in [hypernote/execution_orchestrator.py](/Users/giladrubin/python_workspace/hypernote/hypernote/execution_orchestrator.py)
- runtime ownership in [hypernote/runtime_manager.py](/Users/giladrubin/python_workspace/hypernote/hypernote/runtime_manager.py)
- HTTP handlers in [hypernote/server/handlers.py](/Users/giladrubin/python_workspace/hypernote/hypernote/server/handlers.py)
- Jupyter extension wiring in [hypernote/server/extension.py](/Users/giladrubin/python_workspace/hypernote/hypernote/server/extension.py)
- ephemeral job and attribution ledger in [hypernote/actor_ledger.py](/Users/giladrubin/python_workspace/hypernote/hypernote/actor_ledger.py)

Core rule: notebook edits and execution must operate on one logical document truth whether JupyterLab is closed, already open, or opened mid-run.

Lifecycle rule: notebook contents and outputs persist through Jupyter's `.ipynb` model, but Hypernote's runtime state, job records, and cell attribution are intentionally in-memory and notebook-scoped.

## Shipped Surface

### SDK

- `hypernote.connect(path, create=False)`
- `Notebook`, `CellCollection`, `CellHandle`, `Runtime`, `Job`
- `nb.run(...)`, `nb.run_all()`, `nb.restart()`, `nb.interrupt()`
- `nb.snapshot()`, `nb.status()`, `nb.diff(...)`

### CLI

- `create`
- `ix`
- `exec`
- `edit`
- `run-all`
- `restart`
- `restart-run-all`
- `interrupt`
- `status`
- `diff`
- `cat`
- `job ...`
- `runtime ...`
- `setup doctor`

Default CLI contract:

- TTY: concise human-readable progress
- non-TTY: one compact final JSON result
- explicit streaming only through `--watch` or `--stream-json`

## Rules

- Terminology: when the user says "our system", treat that as the maintained project-operating surface, including `AGENTS.md`, `SKILL.md`, `docs/`, `dev/`, tests, and other shared guidance or verification artifacts around the code.
- SDK first. CLI behavior should come from the SDK, not duplicate raw HTTP semantics.
- One truth. Do not reintroduce a contents-vs-YDoc split for notebook reads or writes.
- Keep control-plane state ephemeral. Do not add durable job history unless the product explicitly needs it.
- Our system is part of done. After every meaningful change, update the relevant parts of `AGENTS.md`, `SKILL.md`, `docs/`, `dev/`, tests, and adjacent shared project guidance so they stay in sync with implementation.
- Keep adapters thin. CLI, JupyterLab, and tests should reuse shared contracts instead of re-encoding notebook behavior.
- Prefer unique notebook paths in tests and demos. Browser tests must also use unique JupyterLab workspace URLs.
- Keep `tmp/` disposable. Durable notes belong in `docs/` or `dev/`, not `tmp/`.

## Read These First

- [SKILL.md](/Users/giladrubin/python_workspace/hypernote/SKILL.md)
- [docs/README.md](/Users/giladrubin/python_workspace/hypernote/docs/README.md)
- [dev/README.md](/Users/giladrubin/python_workspace/hypernote/dev/README.md)

## If You Are Editing...

### `hypernote/sdk.py` or `hypernote/errors.py`

- preserve the notebook-first public object model
- keep public enums and errors stable
- update [docs/sdk.md](/Users/giladrubin/python_workspace/hypernote/docs/sdk.md)

### `hypernote/cli/main.py`

- keep non-TTY output compact by default
- preserve `ix` as the happy-path command
- keep streaming explicit in non-TTY mode
- update [docs/cli.md](/Users/giladrubin/python_workspace/hypernote/docs/cli.md)

### `hypernote/execution_orchestrator.py`, `hypernote/runtime_manager.py`, or `hypernote/server/*`

- preserve the single-truth shared-document path
- verify open-tab and late-open behavior still hold
- update [dev/current-architecture.md](/Users/giladrubin/python_workspace/hypernote/dev/current-architecture.md)
- if browser-visible behavior changes, update [docs/browser-regression-spec.md](/Users/giladrubin/python_workspace/hypernote/docs/browser-regression-spec.md)

### `tests/*`

- prefer the narrowest test that proves the behavior
- preserve coverage for:
  - SDK shape
  - CLI output contract
  - live server behavior
  - browser regression for streaming and late-open correctness

## Verification

Install guidance:

- `uv sync`
  - base Hypernote runtime/server usage
- `uv sync --extra lab`
  - adds the collaborative JupyterLab bundle
- `uv sync --extra dev`
  - adds local development, lint, test, and browser-validation tooling

Minimum checks for most changes:

```bash
uv run ruff check hypernote tests
uv run python -m pytest -q
```

When browser or live-server behavior changes, also use:

```bash
HYPERNOTE_INTEGRATION=1 uv run python -m pytest -q tests/test_live_server.py
uv run python -m pytest -q tests/test_browser_regression.py
```
