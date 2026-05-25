# Hypernote

Notebook-first execution system built on top of Jupyter shared documents.

## Operator Quick Start

Use this path when you are creating, running, recovering, or opening notebooks.
You can stop after this section unless you are changing Hypernote itself.

```bash
uv run hypernote setup doctor
# If no Hypernote API is reachable, start the server from this repo.
# Omit --no-browser when you want setup to open JupyterLab immediately.
uv run hypernote setup serve --no-browser > tmp/hypernote-serve.log 2>&1 &
uv run hypernote setup doctor
notebook_path="tmp/demo-$(date +%Y%m%d-%H%M%S).ipynb"
uv run hypernote create "$notebook_path" --empty --brief
uv run hypernote ix "$notebook_path" -s 'value = 20 + 22; print(value)' --brief
uv run hypernote status "$notebook_path" --brief
```

Use the notebook path the task actually needs. The `tmp/demo-...` path above is
only a disposable example for tests and agent smoke checks.

`--brief` keeps cell `output_preview` while omitting command hints, snapshot
tokens, and bulky raw output payloads. If the preview is too small, use a
focused read instead of reading the whole notebook:

```bash
uv run hypernote cat "$notebook_path" --output CELL_ID --brief --full-output
```

To hand the notebook to a browser, open:

```text
<server-from-setup-doctor>/lab/tree/<notebook-path>
```

## Operator Docs

- [SKILL.md](SKILL.md)
- [docs/README.md](docs/README.md)
- [docs/getting-started.md](docs/getting-started.md)
- [docs/cli.md](docs/cli.md)
- [docs/sdk.md](docs/sdk.md)

## Contributor Docs

If you are editing Hypernote code, tests, architecture, releases, or project
guidance, read [dev/agent-contributor.md](dev/agent-contributor.md). That file
contains the architecture map, code ownership notes, verification matrix, and
release rules intentionally omitted from this operator-facing quick path.
