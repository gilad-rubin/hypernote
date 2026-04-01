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
| NotebookControlAPI | `hypernote/server/` | Jupyter Server extension with REST handlers |
| MCP Server | `hypernote/mcp/` | MCP agent tool surface |
| CLI | `hypernote/cli/` | Command-line interface |
| JupyterLab Extension | `jupyterlab_hypernote/` | Status projection plugin (TypeScript) |

### Ownership boundaries

- **Jupyter** owns: notebook content, outputs, kernels, RTC sync, persistence
- **Hypernote** owns: actor attribution, job tracking, queue visibility, reconnect hints
- **Surfaces** (JupyterLab, MCP, CLI): read/write the same truth, never own it

## Development

```bash
uv sync --all-extras
uv run pytest                    # run tests
uv run ruff check hypernote/     # lint
uv run hypernote --help          # CLI
```

## Conventions

- Python 3.11+, type hints everywhere
- `uv run` for all Python execution
- Async-first: all I/O operations are async
- SQLite via aiosqlite for the ActorLedger
- Tests in `tests/`, pytest + pytest-asyncio
- Conventional commits
