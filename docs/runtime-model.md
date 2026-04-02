# Runtime Model

Jupyter owns the notebook document and kernel primitives. Hypernote owns the control plane around them.

## What Jupyter owns

- notebook persistence
- shared YDoc state
- kernel and session primitives
- notebook rendering in JupyterLab

## What Hypernote owns

- notebook-scoped runtime lifecycle
- job records and waiting/input control
- actor attribution
- SDK and CLI surfaces
- thin HTTP handlers over the shared-document and execution path

## Invariants

- notebook edits and execution must use one logical document truth
- open or closed JupyterLab tabs must not change correctness
- opening a notebook mid-execution should show already-produced output immediately
- late-open should continue streaming without restarting or duplicating execution

## Runtime states

The public SDK exposes `RuntimeStatus` as a stable enum-backed status surface. Clients should use that instead of inferring state from ad hoc HTTP payloads.
