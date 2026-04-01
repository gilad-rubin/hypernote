"""Hypernote MCP Server: agent tool surface.

Exposes notebook operations, execution, jobs, and runtime control as MCP tools.
All tools target notebook IDs and cell IDs — never UI concepts.

Tool families:
- notebook_observe: list notebooks, list cells, read cell, status
- notebook_edit: insert cell, replace cell, delete cell, save
- notebook_execute: queue execution
- notebook_runtime: open, stop, interrupt, status
- jobs: get, list, await, send_stdin
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from hypernote.actor_ledger import ActorLedger, ActorType, JobStatus
from hypernote.execution_orchestrator import (
    ExecutionBackend,
    ExecutionOrchestrator,
    ExecutionResult,
    ExecutionStatus,
    NotebookBackend,
)
from hypernote.runtime_manager import KernelBackend, RuntimeManager, RuntimePolicy

logger = logging.getLogger(__name__)

# Tool definitions organized by family
TOOLS = [
    # --- notebook_observe ---
    Tool(
        name="notebook_list_cells",
        description="List all cells in a notebook with their IDs, types, and sources.",
        inputSchema={
            "type": "object",
            "properties": {"notebook_id": {"type": "string"}},
            "required": ["notebook_id"],
        },
    ),
    Tool(
        name="notebook_read_cell",
        description="Read the source of a specific cell.",
        inputSchema={
            "type": "object",
            "properties": {
                "notebook_id": {"type": "string"},
                "cell_id": {"type": "string"},
            },
            "required": ["notebook_id", "cell_id"],
        },
    ),
    Tool(
        name="notebook_status",
        description="Get notebook runtime status and active jobs.",
        inputSchema={
            "type": "object",
            "properties": {"notebook_id": {"type": "string"}},
            "required": ["notebook_id"],
        },
    ),
    # --- notebook_edit ---
    Tool(
        name="notebook_create",
        description="Create a new notebook at the given path.",
        inputSchema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ),
    Tool(
        name="notebook_open",
        description="Open an existing notebook by path, returns notebook_id.",
        inputSchema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ),
    Tool(
        name="notebook_insert_cell",
        description="Insert a cell at the given index.",
        inputSchema={
            "type": "object",
            "properties": {
                "notebook_id": {"type": "string"},
                "index": {"type": "integer"},
                "cell_type": {"type": "string", "enum": ["code", "markdown"]},
                "source": {"type": "string"},
            },
            "required": ["notebook_id", "index", "source"],
        },
    ),
    Tool(
        name="notebook_replace_cell",
        description="Replace the source of an existing cell.",
        inputSchema={
            "type": "object",
            "properties": {
                "notebook_id": {"type": "string"},
                "cell_id": {"type": "string"},
                "source": {"type": "string"},
            },
            "required": ["notebook_id", "cell_id", "source"],
        },
    ),
    Tool(
        name="notebook_delete_cell",
        description="Delete a cell from the notebook.",
        inputSchema={
            "type": "object",
            "properties": {
                "notebook_id": {"type": "string"},
                "cell_id": {"type": "string"},
            },
            "required": ["notebook_id", "cell_id"],
        },
    ),
    Tool(
        name="notebook_save",
        description="Persist the notebook to disk.",
        inputSchema={
            "type": "object",
            "properties": {"notebook_id": {"type": "string"}},
            "required": ["notebook_id"],
        },
    ),
    # --- notebook_execute ---
    Tool(
        name="notebook_execute",
        description="Queue cells for execution. Returns a job_id for tracking.",
        inputSchema={
            "type": "object",
            "properties": {
                "notebook_id": {"type": "string"},
                "cell_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["notebook_id", "cell_ids"],
        },
    ),
    # --- notebook_runtime ---
    Tool(
        name="runtime_open",
        description="Open or attach to a runtime for a notebook.",
        inputSchema={
            "type": "object",
            "properties": {
                "notebook_id": {"type": "string"},
                "kernel_name": {"type": "string", "default": "python3"},
            },
            "required": ["notebook_id"],
        },
    ),
    Tool(
        name="runtime_stop",
        description="Stop the runtime for a notebook.",
        inputSchema={
            "type": "object",
            "properties": {"notebook_id": {"type": "string"}},
            "required": ["notebook_id"],
        },
    ),
    Tool(
        name="runtime_status",
        description="Get runtime status for a notebook.",
        inputSchema={
            "type": "object",
            "properties": {"notebook_id": {"type": "string"}},
            "required": ["notebook_id"],
        },
    ),
    Tool(
        name="runtime_interrupt",
        description="Interrupt execution for a notebook.",
        inputSchema={
            "type": "object",
            "properties": {"notebook_id": {"type": "string"}},
            "required": ["notebook_id"],
        },
    ),
    # --- jobs ---
    Tool(
        name="job_get",
        description="Get status and details of a specific job.",
        inputSchema={
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    ),
    Tool(
        name="job_list",
        description="List jobs, optionally filtered by notebook_id and status.",
        inputSchema={
            "type": "object",
            "properties": {
                "notebook_id": {"type": "string"},
                "status": {"type": "string"},
            },
        },
    ),
    Tool(
        name="job_await",
        description="Wait for a job to reach a terminal state. Returns final status.",
        inputSchema={
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "timeout_seconds": {"type": "number", "default": 60},
            },
            "required": ["job_id"],
        },
    ),
    Tool(
        name="job_send_stdin",
        description="Send stdin input for a job that is awaiting input.",
        inputSchema={
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["job_id", "value"],
        },
    ),
]

TERMINAL_STATUSES = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.INTERRUPTED}


def _text(data: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(data, default=str))]


class HypernoteMCPServer:
    """MCP server exposing Hypernote operations as tools."""

    def __init__(self, orchestrator: ExecutionOrchestrator, actor_id: str = "mcp-agent"):
        self._orch = orchestrator
        self._actor_id = actor_id
        self._actor_type = ActorType.AGENT
        self._server = Server("hypernote")
        self._register_handlers()

    def _register_handlers(self) -> None:
        @self._server.list_tools()
        async def list_tools() -> list[Tool]:
            return TOOLS

        @self._server.call_tool()
        async def call_tool(name: str, arguments: dict) -> list[TextContent]:
            handler = getattr(self, f"_handle_{name}", None)
            if handler is None:
                return _text({"error": f"Unknown tool: {name}"})
            try:
                result = await handler(arguments)
                return _text(result)
            except Exception as e:
                logger.exception("Tool %s failed", name)
                return _text({"error": str(e)})

    # --- notebook_observe ---

    async def _handle_notebook_list_cells(self, args: dict) -> dict:
        cells = await self._orch.list_cells(args["notebook_id"])
        return {"cells": cells}

    async def _handle_notebook_read_cell(self, args: dict) -> dict:
        source = await self._orch.notebook_backend.get_cell_source(
            args["notebook_id"], args["cell_id"]
        )
        return {"cell_id": args["cell_id"], "source": source}

    async def _handle_notebook_status(self, args: dict) -> dict:
        nb_id = args["notebook_id"]
        runtime = await self._orch.get_runtime_status(nb_id)
        active = await self._orch.list_active_jobs(nb_id)
        return {
            "runtime": runtime,
            "active_jobs": [
                {"job_id": j.job_id, "actor_id": j.actor_id, "status": j.status.value}
                for j in active
            ],
        }

    # --- notebook_edit ---

    async def _handle_notebook_create(self, args: dict) -> dict:
        nb_id = await self._orch.create_notebook(args["path"])
        return {"notebook_id": nb_id}

    async def _handle_notebook_open(self, args: dict) -> dict:
        nb_id = await self._orch.open_notebook(args["path"])
        return {"notebook_id": nb_id}

    async def _handle_notebook_insert_cell(self, args: dict) -> dict:
        cell_id = await self._orch.insert_cell(
            args["notebook_id"],
            args["index"],
            args.get("cell_type", "code"),
            args["source"],
            self._actor_id,
            self._actor_type,
        )
        return {"cell_id": cell_id}

    async def _handle_notebook_replace_cell(self, args: dict) -> dict:
        await self._orch.replace_cell_source(
            args["notebook_id"],
            args["cell_id"],
            args["source"],
            self._actor_id,
            self._actor_type,
        )
        return {"updated": True}

    async def _handle_notebook_delete_cell(self, args: dict) -> dict:
        await self._orch.delete_cell(args["notebook_id"], args["cell_id"])
        return {"deleted": True}

    async def _handle_notebook_save(self, args: dict) -> dict:
        await self._orch.save_notebook(args["notebook_id"])
        return {"saved": True}

    # --- notebook_execute ---

    async def _handle_notebook_execute(self, args: dict) -> dict:
        job = await self._orch.queue_execution(
            args["notebook_id"], args["cell_ids"], self._actor_id, self._actor_type
        )
        return {"job_id": job.job_id, "status": job.status.value}

    # --- notebook_runtime ---

    async def _handle_runtime_open(self, args: dict) -> dict:
        info = await self._orch.runtime_manager.open_runtime(
            args["notebook_id"], f"mcp-{self._actor_id}"
        )
        return {
            "runtime_id": info.runtime_id,
            "state": info.state.value,
            "kernel_id": info.kernel_id,
        }

    async def _handle_runtime_stop(self, args: dict) -> dict:
        rt = self._orch.runtime_manager.get_runtime_for_notebook(args["notebook_id"])
        if rt is None:
            return {"error": "No runtime"}
        info = await self._orch.runtime_manager.stop_runtime(rt.runtime_id)
        return {"state": info.state.value}

    async def _handle_runtime_status(self, args: dict) -> dict:
        return await self._orch.get_runtime_status(args["notebook_id"])

    async def _handle_runtime_interrupt(self, args: dict) -> dict:
        await self._orch.interrupt(args["notebook_id"], self._actor_id, self._actor_type)
        return {"interrupted": True}

    # --- jobs ---

    async def _handle_job_get(self, args: dict) -> dict:
        job = await self._orch.get_job(args["job_id"])
        if job is None:
            return {"error": "Job not found"}
        return {
            "job_id": job.job_id,
            "status": job.status.value,
            "actor_id": job.actor_id,
            "notebook_id": job.notebook_id,
            "target_cells": job.target_cells,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
        }

    async def _handle_job_list(self, args: dict) -> dict:
        status = JobStatus(args["status"]) if args.get("status") else None
        jobs = await self._orch.list_jobs(
            notebook_id=args.get("notebook_id"), status=status
        )
        return {
            "jobs": [
                {"job_id": j.job_id, "status": j.status.value, "actor_id": j.actor_id}
                for j in jobs
            ]
        }

    async def _handle_job_await(self, args: dict) -> dict:
        job_id = args["job_id"]
        timeout = args.get("timeout_seconds", 60)
        deadline = asyncio.get_event_loop().time() + timeout

        while asyncio.get_event_loop().time() < deadline:
            job = await self._orch.get_job(job_id)
            if job is None:
                return {"error": "Job not found"}
            if job.status in TERMINAL_STATUSES:
                return {"job_id": job.job_id, "status": job.status.value}
            await asyncio.sleep(0.5)

        return {"job_id": job_id, "status": "timeout"}

    async def _handle_job_send_stdin(self, args: dict) -> dict:
        await self._orch.send_stdin(
            args["job_id"], args["value"], self._actor_id, self._actor_type
        )
        return {"sent": True}

    async def run_stdio(self) -> None:
        """Run MCP server over STDIO transport."""
        async with stdio_server() as (read, write):
            await self._server.run(read, write, self._server.create_initialization_options())
