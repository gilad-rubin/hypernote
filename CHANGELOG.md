# Changelog

All notable changes to this project will be documented in this file.

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
