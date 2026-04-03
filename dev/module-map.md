# Module Map

## Python package

- [hypernote/sdk.py](hypernote/sdk.py)
  - public notebook-first SDK
- [hypernote/errors.py](hypernote/errors.py)
  - public exceptions
- [hypernote/cli/main.py](hypernote/cli/main.py)
  - agent-first CLI over the SDK, including `setup serve` bootstrap and diagnostics
- [hypernote/execution_orchestrator.py](hypernote/execution_orchestrator.py)
  - shared-document access, job orchestration, execution integration
- [hypernote/runtime_manager.py](hypernote/runtime_manager.py)
  - notebook runtime lifecycle and room state
- [hypernote/server/handlers.py](hypernote/server/handlers.py)
  - HTTP handlers
- [hypernote/server/extension.py](hypernote/server/extension.py)
  - Jupyter server extension wiring
- [hypernote/actor_ledger.py](hypernote/actor_ledger.py)
  - actor attribution

## Release automation

- [.github/workflows/release.yml](.github/workflows/release.yml)
  - tag-driven build, GitHub release, and PyPI publish workflow for the `hypernote` package

## VS Code extension

- [vscode-extension/src/extension.ts](vscode-extension/src/extension.ts)
  - VS Code custom editor, managed JupyterLab launcher, and embedded webview shell

There is no separate JupyterLab-side package that owns notebook semantics. JupyterLab is treated as an external notebook surface attached to the same shared-document and execution path.
