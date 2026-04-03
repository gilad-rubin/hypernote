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

## Server prerequisite

Hypernote CLI and SDK talk to a running Jupyter server with the Hypernote extension enabled.
They do not start Jupyter for you.

For the common local path, bootstrap that server with:

```bash
uv run hypernote setup serve
```

If the notebook belongs to another repo, install Hypernote there (`uv add hypernote --dev`)
and run the same bootstrap command from that repo.

## CLI happy path

```bash
uv run hypernote setup serve
uv run hypernote setup doctor
uv run hypernote create tmp/demo.ipynb
uv run hypernote ix tmp/demo.ipynb -s 'value = 20 + 22\nprint(value)'
uv run hypernote status tmp/demo.ipynb --full
```

Use:

- `ix` when you want to insert and run new code
- `exec` when you want to run existing cells by id
- `edit` when you want to change notebook structure without running it

For batch workflows, prefer this sequence:

1. `create`
2. `exec` the setup/import cell first
3. `ix --cells-file ...` for the remaining cells
4. `cat --no-outputs` after failures to inspect partial notebook state

`ix --cells-file` inserts and executes sequentially. If a code cell fails or awaits input,
later cells may not exist yet.

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
- runtime creation resolves the requested kernel first, otherwise notebook metadata
  `kernelspec.name`, otherwise `python3`
- if notebook metadata changes after a runtime is already live, stop or restart the runtime
  to pick up the new kernelspec
