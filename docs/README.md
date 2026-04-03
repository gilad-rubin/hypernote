# Hypernote Docs

Hypernote is a notebook-first execution layer on top of Jupyter shared documents.

Jupyter owns durable notebook contents and outputs. Hypernote owns an ephemeral control plane for runtimes, jobs, and attribution.

Start here:

- [Getting Started](docs/getting-started.md)
- [CLI Reference](docs/cli.md)
- [SDK Reference](docs/sdk.md)
- [Runtime Model](docs/runtime-model.md)
- [VS Code Extension](docs/vscode-extension.md)
- [Browser Regression Spec](docs/browser-regression-spec.md)

The common operator entrypoint is now `hypernote setup serve`, followed by
`hypernote setup doctor` when you need to confirm server and kernelspec state.

Public docs should describe shipped behavior only. Internal implementation notes belong in `dev/`.
