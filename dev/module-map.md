# Module Map

## Python package

- [src/hypernote/sdk.py](../src/hypernote/sdk.py)
  - public notebook-first SDK
- [src/hypernote/errors.py](../src/hypernote/errors.py)
  - public exceptions
- [src/hypernote/cli/main.py](../src/hypernote/cli/main.py)
  - agent-first CLI over the SDK, including `setup serve` bootstrap and diagnostics
- [src/hypernote/execution_orchestrator.py](../src/hypernote/execution_orchestrator.py)
  - shared-document access, job orchestration, execution integration
- [src/hypernote/runtime_manager.py](../src/hypernote/runtime_manager.py)
  - notebook runtime lifecycle and room state
- [src/hypernote/server/handlers.py](../src/hypernote/server/handlers.py)
  - HTTP handlers
- [src/hypernote/server/extension.py](../src/hypernote/server/extension.py)
  - Jupyter server extension wiring
- [src/hypernote/server/subshell.py](../src/hypernote/server/subshell.py)
  - subshell-aware execute, interrupt, and restart helpers
- [src/hypernote/actor_ledger.py](../src/hypernote/actor_ledger.py)
  - actor attribution

## Release automation

- [.github/workflows/release.yml](../.github/workflows/release.yml)
  - release-PR-triggered build, GitHub release, and PyPI publish workflow for the `hypernote` package, with `workflow_dispatch` retained as a recovery fallback

There is no separate JupyterLab-side package that owns notebook semantics. JupyterLab is treated as an external notebook surface attached to the same shared-document and execution path.
