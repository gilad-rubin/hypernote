# Module Map

## Python package

- [hypernote/sdk.py](/Users/giladrubin/python_workspace/hypernote/hypernote/sdk.py)
  - public notebook-first SDK
- [hypernote/errors.py](/Users/giladrubin/python_workspace/hypernote/hypernote/errors.py)
  - public exceptions
- [hypernote/cli/main.py](/Users/giladrubin/python_workspace/hypernote/hypernote/cli/main.py)
  - agent-first CLI over the SDK
- [hypernote/execution_orchestrator.py](/Users/giladrubin/python_workspace/hypernote/hypernote/execution_orchestrator.py)
  - shared-document access, job orchestration, execution integration
- [hypernote/runtime_manager.py](/Users/giladrubin/python_workspace/hypernote/hypernote/runtime_manager.py)
  - notebook runtime lifecycle and room state
- [hypernote/server/handlers.py](/Users/giladrubin/python_workspace/hypernote/hypernote/server/handlers.py)
  - HTTP handlers
- [hypernote/server/extension.py](/Users/giladrubin/python_workspace/hypernote/hypernote/server/extension.py)
  - Jupyter server extension wiring
- [hypernote/actor_ledger.py](/Users/giladrubin/python_workspace/hypernote/hypernote/actor_ledger.py)
  - actor attribution

## Release automation

- [.github/workflows/release.yml](/Users/giladrubin/python_workspace/hypernote/.github/workflows/release.yml)
  - tag-driven build, GitHub release, and PyPI publish workflow for the `hypernote` package

There is no separate JupyterLab-side package that owns notebook semantics. JupyterLab is treated as an external notebook surface attached to the same shared-document and execution path.
