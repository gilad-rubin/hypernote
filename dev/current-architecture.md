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
- `setup serve` is the CLI bootstrap path for starting a local Hypernote-enabled Jupyter server.
- Notebook reads and writes must go through the shared-document path.
- Execution must resolve cell source from the same document model the UI sees.
- JupyterLab is an optional viewer/actor, not a second source of truth.

## Important consequences

- a newly inserted cell must be immediately executable
- open-tab and closed-tab behavior must match
- opening a notebook mid-run must show prior output and continue streaming
- persisted `.ipynb` output must converge with the live shared document
- runtime creation must resolve the desired kernel from an explicit override, otherwise the
  notebook metadata kernelspec, otherwise `python3`
- Hypernote must not silently reuse a live runtime if the notebook now targets a different kernel

## Late-open via kernel subshells

To make "open a notebook mid-run" reliable with native JupyterLab, Hypernote
routes its own `execute_request` messages through an [ipykernel
subshell](https://jupyter.org/enhancement-proposals/91-kernel-subshells/kernel-subshells.html).
A subshell is a separate handler thread on the kernel side; the kernel's main
shell stays free to answer `kernel_info_request` from any client (e.g. a
JupyterLab tab loading the same notebook), so Lab's notebook UI can finish its
init handshake and render cells while a Hypernote-driven cell is still
executing.

See [hypernote/server/subshell.py](../hypernote/server/subshell.py) and
[tests/test_subshell.py](../tests/test_subshell.py) for the mechanism and the
load-bearing latency assertion. Subshells require ipykernel 7+ (IPython
kernels). Other kernels fall back to the main shell; late-open during a
long-running cell will block Lab UI for those kernels.
