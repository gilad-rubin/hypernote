# Current Architecture

Hypernote is a notebook-first layer over Jupyter shared documents.

## Topology

```text
Agent or Human
    ↕
CLI / SDK / JupyterLab
    ↕
Hypernote HTTP handlers + execution orchestration
    ↕
Jupyter shared document + kernel/session primitives
```

## Current design

- The SDK is the public semantic center.
- The CLI is a thin shell over the SDK.
- `setup serve` is the CLI bootstrap path for starting a local Hypernote-enabled JupyterLab server.
- Notebook reads and writes must go through the shared-document path.
- Execution must resolve cell source from the same document model the UI sees.
- Servers launched by `setup serve` use Jupyter's temporary RTC collaboration journal;
  the `.ipynb` file is the durable notebook artifact.
- JupyterLab is the supported integration environment. Opening a Lab tab is optional,
  but Hypernote still runs through the Hypernote-enabled JupyterLab server and shared
  document path.

## Important consequences

- a newly inserted cell must be immediately executable
- open-tab and closed-tab behavior must match
- opening a notebook mid-run must show prior output and continue streaming
- persisted `.ipynb` output must converge with the live shared document
- unsaved shared-document state is not recovered through a project-local RTC database
- runtime creation must resolve the desired kernel from an explicit override, otherwise the
  notebook metadata kernelspec, otherwise `python3`
- Hypernote must not silently reuse a live runtime if the notebook now targets a different kernel

## Concurrent-actor model: subshells + route overrides

The product invariant is that JupyterLab and Hypernote can both drive the
same notebook through one shared kernel session. Achieving this with native
JupyterLab requires three coordinated mechanisms inside Hypernote.

### 1. Subshell-routed execute (so late-open works)

Hypernote routes its `execute_request` messages through an [ipykernel
subshell](https://jupyter.org/enhancement-proposals/91-kernel-subshells/kernel-subshells.html).
A subshell is a separate handler thread on the kernel side; the kernel's
main shell stays free to answer `kernel_info_request` from any client (e.g.
a JupyterLab tab loading the same notebook), so Lab's notebook UI can
finish its init handshake and render cells while a Hypernote-driven cell
is still executing.

### 2. Subshell-aware interrupt (so the Stop button works)

ipykernel 7.2's `interrupt_request` ignores the JEP 91 `subshell_id` and
sends process-wide SIGINT, which only reaches the kernel's main thread —
not the subshell. Hypernote's extension overrides the
`POST /api/kernels/{id}/interrupt` route. When a Hypernote runtime owns
the kernel, the override sends a small Python snippet on the main shell
that uses `ctypes.pythonapi.PyThreadState_SetAsyncExc` to raise
`KeyboardInterrupt` in the subshell thread (looked up by the
`subshell-<id>` thread name). For non-Hypernote kernels the override
falls through to the default SIGINT path.

### 3. Restart-with-cleanup (so subsequent runs work)

Explicit restart via `POST /api/kernels/{id}/restart` does NOT fire the
autorestarter callbacks, so Hypernote's `subshell_id` cache and nbmodel's
`AsyncKernelClient` cache both go stale unless something cleans them up.
Hypernote's extension also overrides the restart route. After the kernel
restart succeeds, `cleanup_after_restart` cancels nbmodel's worker task,
stops channels on the cached client, and evicts the per-kernel entries
from nbmodel's caches. The next execute through the orchestrator builds a
fresh client and a fresh subshell against the new kernel process.

See [src/hypernote/server/subshell.py](../src/hypernote/server/subshell.py)
and [src/hypernote/server/handlers.py](../src/hypernote/server/handlers.py)
for the implementations. Browser regression tests in
[tests/test_browser_regression.py](../tests/test_browser_regression.py)
prove all three behaviors over a real JupyterLab page.

Subshells require ipykernel 7+ (IPython kernels). Other kernels fall back
to the main shell, which means late-open during a long-running cell will
block the Lab UI for those kernels and the subshell-targeted interrupt
becomes a no-op (the route override falls through to SIGINT, which works
on the main shell).
