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

Hypernote-owned control-plane state is intentionally ephemeral:

- runtimes are in-memory and notebook-scoped
- job records are for live coordination and recent status, not durable audit history
- cell attribution currently lives in the same in-memory control plane
- stopping a runtime, GC eviction, or restarting the server clears that control-plane state

Notebook contents and outputs still persist because Jupyter owns the `.ipynb` document.

## Kernel selection

Runtime creation resolves the desired kernel in this order:

1. explicit kernel override from the caller
2. notebook metadata `kernelspec.name`
3. `"python3"`

If a runtime is already live and the notebook later points at a different kernelspec,
Hypernote rejects silent reuse. Stop or restart the runtime to pick up the new kernel.

## Invariants

- notebook edits and execution must use one logical document truth
- open or closed JupyterLab tabs must not change correctness
- opening a notebook mid-execution should show already-produced output immediately
- late-open should continue streaming without restarting or duplicating execution
- clients must not treat job history or attribution as durable across runtime stop or server restart

## Runtime states

The public SDK exposes `RuntimeStatus` as a stable enum-backed status surface. Clients should use that instead of inferring state from ad hoc HTTP payloads.
