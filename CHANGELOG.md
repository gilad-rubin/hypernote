# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

### Added

- added a minimal VS Code extension under `vscode-extension/` that opens JupyterLab in a VS Code custom editor or panel
- added managed local JupyterLab startup for the extension when no configured server is reachable

### Changed

- release workflow switched from tag-triggered to `workflow_dispatch` — run `gh workflow run release.yml -f version=X.Y.Z` to release

### Notes

- the extension is intentionally decoupled from Hypernote-specific UI and connects to plain JupyterLab
- managed extension launches override Jupyter's default frame-ancestor policy so the embedded view can load inside VS Code

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
