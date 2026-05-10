# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

## 0.4.0 - 2026-05-10

Hypernote now treats Jupyter's real-time collaboration journal as temporary
server-local state for `setup serve`, keeping the `.ipynb` notebook file as the
only durable project artifact.

### Changed

- `hypernote setup serve` now configures Jupyter RTC to use temporary
  collaboration journal storage instead of Jupyter's default project-root
  `.jupyter_ystore.db` SQLite database.
- Live-server and browser regression fixtures now launch with the same
  temporary journal policy as `setup serve`, including coverage that notebook
  execution does not create `.jupyter_ystore.db` in the server root.
- Project guidance now distinguishes the durable **Notebook File** from the
  temporary **Collaboration Journal**, and documents the crash-recovery tradeoff
  for unsaved live shared-document changes.

## 0.3.0 - 2026-05-10

Hypernote is now a JupyterLab-first integration: the default install carries the
collaboration/docprovider stack, `setup serve` opens Lab by default, and
`setup doctor` can distinguish API reachability from shared-document and Lab
frontend health.

### Changed

- Hypernote is now packaged and documented as a JupyterLab-first integration:
  the default install includes JupyterLab collaboration support, `setup serve`
  opens Lab by default, `setup doctor` reports the shared-document stack and
  duplicate local servers, and cell-state operations require the shared
  document path instead of falling back to contents-manager edits.

### Notes

- correction: the 0.2.0 headline mentions "an experimental VS Code extension" but the
  `vscode-extension/` work was never committed and did not ship in the 0.2.0 artifact.
  Documentation referring to the VS Code extension has been removed from `README.md`,
  `AGENTS.md`, `SKILL.md`, `docs/README.md`, `dev/README.md`, and `dev/module-map.md`.

## 0.2.0 - 2026-05-07

Native JupyterLab as a first-class concurrent actor: open a notebook
mid-run and see streaming output, click Stop and the cell terminates,
click Restart and the kernel comes back ready. Plus the package now ships
in PyPA src layout and an experimental VS Code extension.

### Added

- subshell-routed kernel execution (JEP 91 / ipykernel 7+): Hypernote sends `execute_request` with a `subshell_id` so the kernel's main shell stays free to answer concurrent clients (e.g. JupyterLab) — opening a notebook mid-run now renders cells immediately and continues to stream output without waiting for the running cell
- subshell-aware `POST /api/kernels/{id}/interrupt` override: JupyterLab's Stop button (and `nb.interrupt()`) now terminates a Hypernote-driven cell, raising `KeyboardInterrupt` in the subshell thread via a main-shell `PyThreadState_SetAsyncExc` injection. Falls back to default SIGINT for non-Hypernote-driven kernels.
- subshell- and nbmodel-aware `POST /api/kernels/{id}/restart` override: JupyterLab's Restart button now leaves the kernel ready to run new cells. Hypernote evicts nbmodel's stale per-kernel client and worker, and clears its cached subshell id, so the next execute rebuilds against the fresh kernel.
- new browser regression tests for Lab Stop and Lab Restart against subshell-routed cells; new real-kernel unit tests for subshell creation, routing, interrupt latency, and restart cleanup.

### Changed

- package layout migrated to `src/hypernote/` (PyPA src layout). `pyproject.toml` now configures `[tool.hatch.build.targets.wheel] packages = ["src/hypernote"]` and `[tool.ruff] src = ["src", "tests"]`. The `hypernote` import name is unchanged — `import hypernote` continues to work.
- release workflow switched from tag-triggered to `workflow_dispatch` — run `gh workflow run release.yml -f version=X.Y.Z` to release

### Notes

- subshell routing requires ipykernel 7+ (IPython kernels). Other kernels fall back to the main shell — late-open during a long-running cell will block the JupyterLab UI for those kernels and the subshell-targeted interrupt becomes a no-op (override falls through to SIGINT, which works on the main shell).
- `PyThreadState_SetAsyncExc`-based interrupt cannot terminate a thread inside a long blocking C call without GIL release (e.g. `requests.get` with no timeout). The `KeyboardInterrupt` only fires once control returns to Python. The interrupt snippet falls back to `os.kill(pid, SIGINT)` internally if the subshell thread cannot be found, so user-visible "Stop did nothing" cases degrade to default SIGINT behavior rather than silent failure.

## 0.1.3 - 2026-04-03

Cross-repo runtime hardening and agent ergonomics.

### Added

- `hypernote setup serve` — bootstraps a Hypernote-enabled Jupyter server with all required extensions
- `hypernote setup doctor --path PATH` — reports notebook kernelspec, live runtime kernel, resolved launcher, and warns on mismatches
- `hypernote create --empty` — removes any default cells Jupyter auto-inserts so notebooks start clean
- batch `ix` output now includes `cells_inserted`, `cells_total`, `cells_remaining`, `halt_reason`, and `last_processed_cell_id` on early halt
- timeout errors now surface job id, last known status, and recovery hints pointing to `job get` and `cat`
- runtime kernel mismatch detection — clear error when a live runtime's kernel doesn't match notebook metadata, with guidance to restart

### Changed

- execution now resolves kernels as: explicit override > notebook metadata `kernelspec.name` > `python3`
- `RuntimeManager.ensure_room()` rejects silent reuse when a live room exists with a different kernel name
- removed all hardcoded local paths from project markdown files — links are now repo-relative
- SKILL.md rewritten: iterative `ix → observe → ix` is the primary workflow, heredoc/stdin documented for multi-line cells, server lifecycle section added, cross-repo setup simplified to `uv add hypernote --dev`

### Notes

- `--cells-file` batch mode is now documented as a convenience for known-good sequences, not the default workflow
- the recommended cross-repo pattern is now: install hypernote in the target repo, not `uv run --with`

## 0.1.1 - 2026-04-02

Patch release focused on release automation hardening.

### Changed

- updated the GitHub release workflow to newer official action majors where available
- replaced the third-party GitHub release action with the `gh` CLI in release automation
- fixed the release workflow to install Playwright browser binaries before running browser tests
- fixed the release workflow to run tests with the `dev` extra installed
- fixed the PyPI publish step to use token-only `uv publish` authentication

### Notes

- package code is unchanged from `0.1.0`
- this release exists to verify and stabilize the automated release path

## 0.1.0 - 2026-04-02

Initial public release.

### Added

- notebook-first Python SDK built around `Notebook`, `CellCollection`, `CellHandle`, `Runtime`, and `Job`
- agent-first CLI with `create`, `ix`, `exec`, `edit`, `run-all`, `restart`, `interrupt`, `status`, `diff`, `cat`, `job`, and `runtime` flows
- Jupyter server extension with narrow Hypernote REST handlers
- notebook-scoped runtime lifecycle with attach, detach, recovery, stop, and GC
- headless execution flow over Jupyter shared documents and `jupyter-server-nbmodel`
- job polling and `input()` round-trip support for long-running or interactive execution
- live-server and browser regression tests for late-open, persistence, and shared-document correctness
- project and developer docs for the runtime model, SDK, CLI, and release workflow

### Changed

- aligned the control plane around an explicitly ephemeral lifecycle
- moved job tracking and cell attribution to an in-memory notebook-scoped ledger
- removed the SQLite dependency and any implied durable job history

### Notes

- Jupyter owns durable notebook contents and outputs in `.ipynb`
- Hypernote owns ephemeral runtime state, jobs, and attribution
