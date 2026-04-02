# Getting Started

## Install

Pick the smallest install that matches what you need:

- `hypernote`
  - notebook-first SDK, CLI, Jupyter server extension, and shared-document execution path
- `hypernote[lab]`
  - base install plus JupyterLab collaboration support
- `hypernote[dev]`
  - base install plus local development, test, lint, and browser-validation tooling

Examples:

```bash
uv sync
uv sync --extra lab
uv sync --extra dev
```

## CLI happy path

```bash
uv run hypernote create tmp/demo.ipynb
uv run hypernote ix tmp/demo.ipynb -s 'value = 20 + 22\nprint(value)'
uv run hypernote status tmp/demo.ipynb --full
```

Use:

- `ix` when you want to insert and run new code
- `exec` when you want to run existing cells by id
- `edit` when you want to change notebook structure without running it

## SDK happy path

```python
import hypernote

nb = hypernote.connect("tmp/demo.ipynb", create=True)
cell = nb.cells.insert_code("value = 20 + 22\nprint(value)", id="hello")
job = cell.run()
job.wait()

print(nb.status(full=True).summary)
```

## Key guarantee

Hypernote uses one logical notebook truth. A notebook may be:

- closed
- already open in JupyterLab
- opened mid-execution

The notebook state, execution state, and outputs should still agree.

## Lifecycle expectation

- notebook contents and outputs persist in the `.ipynb` because Jupyter owns the document
- runtime state, jobs, and cell attribution are ephemeral Hypernote control-plane state
- stopping a runtime or restarting the server clears that control-plane state
