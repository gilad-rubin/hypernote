# Hypernote

Server-owned notebook execution with actor attribution, queue visibility, and MCP agent surface.

## Architecture

See `tmp/codex_consolidated.md` for the full architecture document.

Core rule: **The notebook is a server-owned shared document with server-owned execution, and every client — human or agent — is just an attached actor.**

### Components

| Component | Location | Purpose |
|---|---|---|
| ActorLedger | `hypernote/actor_ledger.py` | SQLite persistence for job/attribution metadata |
| RuntimeManager | `hypernote/runtime_manager.py` | Server-side runtime lifecycle (attach/detach/GC) |
| ExecutionOrchestrator | `hypernote/execution_orchestrator.py` | Thin wrapper over Jupyter execution with attribution |
| NotebookControlAPI | `hypernote/server/handlers.py` | REST handlers (tornado) for notebook/cell/job/runtime operations |
| Server Extension | `hypernote/server/extension.py` | Jupyter Server extension wiring |
| Jupyter Backends | `hypernote/server/jupyter_backends.py` | Concrete wrappers for Jupyter kernel/contents managers |
| MCP Server | `hypernote/mcp/server.py` | 18 MCP tools across 5 families |
| CLI | `hypernote/cli/main.py` | 8 command families, 30+ subcommands |
| JupyterLab Extension | `jupyterlab_hypernote/` | Status projection plugin (TypeScript) |

### Three-layer model

```
Surfaces (JupyterLab, MCP, CLI) — read/write the same truth, never own it
    |
Product Thin Layer (ActorLedger + RuntimeManager + ExecutionOrchestrator)
    |
Jupyter Base (Server + RTC + jupyter-server-nbmodel + kernels)
```

### Ownership boundaries

- **Jupyter** owns: notebook content, outputs, kernels, RTC sync, persistence
- **Hypernote** owns: actor attribution, job tracking, queue visibility, reconnect hints
- **Surfaces** (JupyterLab, MCP, CLI): read/write the same truth, never own it

## Development

```bash
uv sync --all-extras                           # install all deps
uv run python -m pytest tests/ -v              # run all 63 tests
uv run python -m pytest tests/test_integration.py -v  # integration flows
uv run ruff check hypernote/                   # lint
uv run hypernote --help                        # CLI
```

### Running with live Jupyter Server

```bash
# Start Jupyter Server with Hypernote extension
uv run jupyter server --ServerApp.jpserver_extensions='{"hypernote": true}'

# Integration tests against live server
HYPERNOTE_INTEGRATION=1 uv run python -m pytest tests/test_browser_validation.py -v

# Browser validation (requires playwright)
HYPERNOTE_INTEGRATION=1 HYPERNOTE_BROWSER_TEST=1 uv run python -m pytest tests/test_browser_validation.py::test_browser_parity -v
```

### MCP Server

```bash
# Run MCP server over STDIO
uv run python -m hypernote.mcp.server
```

## Test coverage

| Module | Tests | What's covered |
|---|---|---|
| `test_actor_ledger.py` | 8 | Job CRUD, status lifecycle, cell attribution, upsert |
| `test_runtime_manager.py` | 13 | Open/attach/detach/stop, GC sweep, pinning, shutdown |
| `test_execution_orchestrator.py` | 8 | Queue execution, attribution, runtime creation, errors |
| `test_handlers.py` | 7 | REST endpoint cycle: create, cells, execute, jobs, runtime |
| `test_mcp_server.py` | 9 | All MCP tool families exercised |
| `test_cli.py` | 11 | All 8 command groups + subcommands verified |
| `test_integration.py` | 7 | 5 architecture flows + 2 acceptance criteria |
| **Total** | **63** | |

## CLI families

| Family | Commands |
|---|---|
| `observe` | cat, status, list |
| `edit` | insert, replace, delete, clear |
| `execute` | cell, run-all, insert-and-run, restart, interrupt |
| `jobs` | get, list, await, send-stdin |
| `runtime` | status, open, stop, recover |
| `checkpoints` | create, list, restore, delete |
| `workspace` | open, list |
| `setup` | doctor, mcp-status |

## MCP tool families

| Family | Tools |
|---|---|
| `notebook_observe` | list_cells, read_cell, status |
| `notebook_edit` | create, open, insert_cell, replace_cell, delete_cell, save |
| `notebook_execute` | execute |
| `notebook_runtime` | open, stop, status, interrupt |
| `jobs` | get, list, await, send_stdin |

## Conventions

- Python 3.12+, type hints everywhere
- `uv run python -m pytest` for tests (uses venv python, not system)
- Async-first: all I/O operations are async
- SQLite via aiosqlite for the ActorLedger
- Tests in `tests/`, pytest + pytest-asyncio, asyncio_mode=auto
- Conventional commits
- Actor identity via `X-Hypernote-Actor-Id` / `X-Hypernote-Actor-Type` headers
