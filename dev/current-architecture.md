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
