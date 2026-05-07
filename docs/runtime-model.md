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
- JupyterLab's Stop button must terminate a Hypernote-driven cell
- JupyterLab's Restart must leave the kernel ready to run new cells
- clients must not treat job history or attribution as durable across runtime stop or server restart

## Concurrent kernel access

Hypernote and JupyterLab share one notebook session and one kernel.
Hypernote-driven cells run in a per-kernel subshell so they do not block
the kernel's main shell — that is what lets a JupyterLab tab opened
mid-run answer its own `kernel_info_request` and render the notebook UI
while a cell is still executing. See `dev/current-architecture.md` for
the full mechanism.

Native JupyterLab toolbar/keyboard actions are wired transparently:

- **Stop / interrupt** — Hypernote's extension overrides
  `POST /api/kernels/{id}/interrupt`. When a Hypernote runtime owns the
  kernel, the override raises `KeyboardInterrupt` in the subshell thread
  via `PyThreadState_SetAsyncExc`. For non-Hypernote kernels it falls
  back to the default `KernelManager.interrupt_kernel` (process SIGINT).
- **Restart** — Hypernote's extension overrides
  `POST /api/kernels/{id}/restart`. After the kernel process restarts the
  override evicts nbmodel's stale kernel client + worker and clears the
  cached subshell id. The next execute rebuilds against the fresh kernel.
- **Run cell from Lab** — Lab's `notebook:run-cell` posts an ordinary
  `execute_request` on the kernel's main shell (no `subshell_id`), which
  is the unblocked side. It runs concurrently with whatever Hypernote is
  doing on the subshell. Because they share `globals()`, take care if
  agent and human manipulate the same variables simultaneously.

Subshells are a kernel-side feature (ipykernel 7+, JEP 91). For non-IPython
kernels Hypernote falls back to the main shell; late-open during a
long-running cell will block the JupyterLab UI for those kernels and the
subshell-targeted interrupt is a no-op (the route override falls through
to SIGINT).

## Runtime states

The public SDK exposes `RuntimeStatus` as a stable enum-backed status surface. Clients should use that instead of inferring state from ad hoc HTTP payloads.
