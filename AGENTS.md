# Hypernote

Notebook-first execution system built on top of Jupyter shared documents

## Quick Start

```bash
uv sync --extra dev
uv run hypernote
uv run hypernote --help
uv run hypernote setup serve
uv run hypernote setup doctor
uv run hypernote create tmp/demo.ipynb
uv run hypernote ix tmp/demo.ipynb -s 'value = 20 + 22\nprint(value)'
uv run hypernote status tmp/demo.ipynb --full
uv run python -m pytest -q
uv run ruff check src/hypernote tests
```

## Architecture

Jupyter owns notebook truth:

- `.ipynb` persistence
- shared YDoc document state
- kernel and session primitives
- notebook rendering in JupyterLab

Hypernote owns a thin control plane:

- public SDK in [src/hypernote/sdk.py](src/hypernote/sdk.py)
- public errors in [src/hypernote/errors.py](src/hypernote/errors.py)
- agent-first CLI in [src/hypernote/cli/main.py](src/hypernote/cli/main.py)
- execution orchestration and shared-document mutation in [src/hypernote/execution_orchestrator.py](src/hypernote/execution_orchestrator.py)
- runtime ownership in [src/hypernote/runtime_manager.py](src/hypernote/runtime_manager.py)
- HTTP handlers in [src/hypernote/server/handlers.py](src/hypernote/server/handlers.py)
- Jupyter extension wiring in [src/hypernote/server/extension.py](src/hypernote/server/extension.py)
- ephemeral job and attribution ledger in [src/hypernote/actor_ledger.py](src/hypernote/actor_ledger.py)
- subshell-aware execute/interrupt/restart in [src/hypernote/server/subshell.py](src/hypernote/server/subshell.py)

Core rule: notebook edits and execution must operate on one logical document truth whether JupyterLab is closed, already open, or opened mid-run.

Lifecycle rule: notebook contents and outputs persist through Jupyter's `.ipynb` model, but Hypernote's runtime state, job records, and cell attribution are intentionally in-memory and notebook-scoped.

Concurrent-actor rule: JupyterLab and Hypernote share one notebook session and one kernel. Hypernote-driven cells run in an ipykernel subshell so the kernel's main shell stays responsive to native Lab actions. Hypernote's extension overrides `/api/kernels/{id}/interrupt` and `/api/kernels/{id}/restart` so Lab's Stop and Restart toolbar buttons reach the subshell-routed cell or perform the right cleanup. See [dev/current-architecture.md](dev/current-architecture.md) for the full mechanism.

## Shipped Surface

### SDK

- `hypernote.connect(path, create=False)`
- `Notebook`, `CellCollection`, `CellHandle`, `Runtime`, `Job`
- `nb.run(...)`, `nb.run_all()`, `nb.restart()`, `nb.interrupt()`
- `nb.snapshot()`, `nb.status()`, `nb.diff(...)`

### CLI

- `hypernote` — live workspace dashboard with hints
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
- `setup serve`

Default CLI contract:

- bare `hypernote` shows live workspace state and next-step hints
- TTY: concise human-readable progress
- non-TTY: one compact final JSON result
- explicit streaming only through `--watch` or `--stream-json`
- summary-first read payloads should come from SDK-backed observation helpers, not CLI-only formatting rules
- `setup serve` is the default local bootstrap path for a Hypernote-enabled JupyterLab server
- `setup doctor --path PATH` is the preferred first diagnostic when server reachability,
  kernelspec selection, or runtime mismatch is unclear

## Rules

- Terminology: when the user says "our system", treat that as the maintained project-operating surface, including `AGENTS.md`, `SKILL.md`, `docs/`, `dev/`, tests, and other shared guidance or verification artifacts around the code.
- SDK first. CLI behavior should come from the SDK, not duplicate raw HTTP semantics.
- When CLI and SDK both need compact observation behavior, define the summary/truncation/focused-read logic in the SDK and let the CLI adapt it.
- Shared logic needs one owner. When a helper or shaping rule moves into the SDK or another shared layer, delete the old copies instead of letting multiple versions drift.
- One truth. Do not reintroduce a contents-vs-YDoc split for notebook reads or writes.
- Runtime creation must honor the requested kernel first, otherwise notebook metadata
  `kernelspec.name`, otherwise `python3`.
- Keep control-plane state ephemeral. Do not add durable job history unless the product explicitly needs it.
- Our system is part of done. After every meaningful change, update the relevant parts of `AGENTS.md`, `SKILL.md`, `docs/`, `dev/`, tests, and adjacent shared project guidance so they stay in sync with implementation.
- Keep adapters thin. CLI, JupyterLab, and tests should reuse shared contracts instead of re-encoding notebook behavior.
- Treat output and API shapes as contracts. Feature variants should preserve the same top-level envelope and field semantics unless there is a deliberate, documented exception.
- Normalize boundary inputs early. If upstream payloads can arrive in more than one valid shape, accept and normalize them at the adapter boundary rather than assuming a single representation.
- Prefer unique notebook paths in tests and demos. Browser tests must also use unique JupyterLab workspace URLs.
- Keep `tmp/` disposable. Durable notes belong in `docs/` or `dev/`, not `tmp/`.
- Release every version through a PR (CHANGELOG move + version bump + lockfile refresh on a `release/vX.Y.Z` branch), never via direct push to master. The full process is in [dev/release.md](dev/release.md).

## Read These First

- [SKILL.md](SKILL.md)
- [docs/README.md](docs/README.md)
- [dev/README.md](dev/README.md)
- [dev/release.md](dev/release.md)

## If You Are Editing...

### `src/hypernote/sdk.py` or `src/hypernote/errors.py`

- preserve the notebook-first public object model
- keep public enums and errors stable
- prefer adding reusable observation helpers on `NotebookStatus` / `CellStatus` over adding CLI-only shaping logic
- update [docs/sdk.md](docs/sdk.md)

### `src/hypernote/cli/main.py`

- keep non-TTY output compact by default
- keep bare `hypernote` as the live dashboard view
- preserve `ix` as the happy-path command
- preserve summary-first `status` and compact `cat` with contextual hints
- prefer rendering SDK observation helpers over introducing new CLI-only data shaping
- keep streaming explicit in non-TTY mode
- update [docs/cli.md](docs/cli.md)

### `src/hypernote/execution_orchestrator.py`, `src/hypernote/runtime_manager.py`, or `src/hypernote/server/*`

- preserve the single-truth shared-document path
- verify open-tab and late-open behavior still hold
- update [dev/current-architecture.md](dev/current-architecture.md)
- if browser-visible behavior changes, update [docs/browser-regression-spec.md](docs/browser-regression-spec.md)

### `tests/*`

- prefer the narrowest test that proves the behavior
- preserve coverage for:
  - SDK shape
  - CLI output contract
  - live server behavior
  - browser regression for streaming and late-open correctness
- for contract-heavy changes, add invariant coverage for:
  - default and focused variants
  - empty and failure states
  - alternate valid input shapes from upstream payloads
  - parity between real helpers and any fake/test-double implementations

### `pyproject.toml` version, `CHANGELOG.md`, or `.github/workflows/release.yml`

- always use the PR-based release process in [dev/release.md](dev/release.md)
- do not push release-prep commits directly to master, even for a one-line version bump
- before opening the release PR, confirm `git ls-tree origin/master` shows every file mentioned in the new CHANGELOG section — local-only work must not be claimed in the changelog
- if you change the release workflow shape (steps, secrets, version source), update [dev/release.md](dev/release.md) in the same PR so the doc stays accurate

## Verification

Install guidance:

- `uv sync`
  - Hypernote's default JupyterLab integration stack
- `uv sync --extra dev`
  - adds local development, lint, test, and browser-validation tooling

Minimum checks for most changes:

```bash
uv run ruff check src/hypernote tests
uv run python -m pytest -q
```

When browser or live-server behavior changes, also use:

```bash
HYPERNOTE_INTEGRATION=1 uv run python -m pytest -q tests/test_live_server.py
uv run python -m pytest -q tests/test_browser_regression.py
```

Before opening or updating a PR for a cross-surface change, do a short contract pass:

- check that sibling command variants still share one consistent envelope
- check that aggregate field names still match their exact semantics
- check that docs and hints only mention shipped commands and flags
- check that moved logic no longer has stale copies in adapters or tests
