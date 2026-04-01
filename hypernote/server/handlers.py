"""REST handlers for the Hypernote NotebookControlAPI.

All handlers target notebook IDs, cell IDs, and runtime IDs.
None target UI concepts like "active notebook" or "focused editor".
"""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any, Callable, Awaitable

import tornado.web

from hypernote.actor_ledger import ActorType, JobStatus
from hypernote.execution_orchestrator import ExecutionOrchestrator


class BaseHypernoteHandler(tornado.web.RequestHandler):
    """Base handler with orchestrator access."""

    def initialize(self, get_orchestrator: Callable[[], Awaitable[ExecutionOrchestrator]]) -> None:
        self._get_orchestrator = get_orchestrator

    async def get_orch(self) -> ExecutionOrchestrator:
        return await self._get_orchestrator()

    def get_actor(self) -> tuple[str, ActorType]:
        """Extract actor identity from request headers or body."""
        actor_id = self.request.headers.get("X-Hypernote-Actor-Id", "anonymous")
        actor_type_str = self.request.headers.get("X-Hypernote-Actor-Type", "human")
        actor_type = ActorType(actor_type_str) if actor_type_str in ("human", "agent") else ActorType.HUMAN
        return actor_id, actor_type

    def write_json(self, data: Any, status: int = 200) -> None:
        self.set_status(status)
        self.set_header("Content-Type", "application/json")
        self.finish(json.dumps(data, default=str))

    def get_json_body(self) -> dict:
        try:
            return json.loads(self.request.body)
        except (json.JSONDecodeError, TypeError):
            return {}


# --- Notebook handlers ---


class NotebooksHandler(BaseHypernoteHandler):
    """POST: create notebook, GET: list (placeholder)."""

    async def post(self) -> None:
        body = self.get_json_body()
        path = body.get("path", "untitled.ipynb")
        orch = await self.get_orch()
        nb_id = await orch.create_notebook(path)
        self.write_json({"notebook_id": nb_id}, status=HTTPStatus.CREATED)

    async def get(self) -> None:
        # Listing notebooks is a Jupyter Server concern, not Hypernote's
        self.write_json({"notebooks": []})


class NotebookHandler(BaseHypernoteHandler):
    """GET: open/info for a notebook."""

    async def get(self, notebook_id: str) -> None:
        orch = await self.get_orch()
        try:
            nb_id = await orch.open_notebook(notebook_id)
            cells = await orch.list_cells(nb_id)
            self.write_json({"notebook_id": nb_id, "cells": cells})
        except Exception as e:
            raise tornado.web.HTTPError(404, reason=str(e))


class SaveHandler(BaseHypernoteHandler):
    """POST: save notebook to disk."""

    async def post(self, notebook_id: str) -> None:
        orch = await self.get_orch()
        await orch.save_notebook(notebook_id)
        self.write_json({"saved": True})


# --- Cell handlers ---


class CellsHandler(BaseHypernoteHandler):
    """GET: list cells, POST: insert cell."""

    async def get(self, notebook_id: str) -> None:
        orch = await self.get_orch()
        cells = await orch.list_cells(notebook_id)
        self.write_json({"cells": cells})

    async def post(self, notebook_id: str) -> None:
        body = self.get_json_body()
        index = body.get("index", 0)
        cell_type = body.get("cell_type", "code")
        source = body.get("source", "")
        actor_id, actor_type = self.get_actor()
        orch = await self.get_orch()
        cell_id = await orch.insert_cell(
            notebook_id, index, cell_type, source, actor_id, actor_type
        )
        self.write_json({"cell_id": cell_id}, status=HTTPStatus.CREATED)


class CellHandler(BaseHypernoteHandler):
    """PUT: replace cell source, DELETE: delete cell."""

    async def put(self, notebook_id: str, cell_id: str) -> None:
        body = self.get_json_body()
        source = body.get("source", "")
        actor_id, actor_type = self.get_actor()
        orch = await self.get_orch()
        await orch.replace_cell_source(notebook_id, cell_id, source, actor_id, actor_type)
        self.write_json({"updated": True})

    async def delete(self, notebook_id: str, cell_id: str) -> None:
        orch = await self.get_orch()
        await orch.delete_cell(notebook_id, cell_id)
        self.write_json({"deleted": True})


class CellAttributionHandler(BaseHypernoteHandler):
    """GET: cell attribution info."""

    async def get(self, notebook_id: str, cell_id: str) -> None:
        orch = await self.get_orch()
        attr = await orch.ledger.get_cell_attribution(notebook_id, cell_id)
        if attr is None:
            self.write_json({})
            return
        self.write_json({
            "last_editor_id": attr.last_editor_id,
            "last_editor_type": attr.last_editor_type,
            "last_executor_id": attr.last_executor_id,
            "last_executor_type": attr.last_executor_type,
            "updated_at": attr.updated_at,
        })


# --- Execution handlers ---


class ExecuteHandler(BaseHypernoteHandler):
    """POST: queue execution for cells."""

    async def post(self, notebook_id: str) -> None:
        body = self.get_json_body()
        cell_ids = body.get("cell_ids", [])
        if not cell_ids:
            raise tornado.web.HTTPError(400, reason="cell_ids required")
        actor_id, actor_type = self.get_actor()
        orch = await self.get_orch()
        job = await orch.queue_execution(notebook_id, cell_ids, actor_id, actor_type)
        self.write_json({
            "job_id": job.job_id,
            "status": job.status.value,
            "notebook_id": notebook_id,
        }, status=HTTPStatus.ACCEPTED)


class JobsHandler(BaseHypernoteHandler):
    """GET: list jobs with optional filters."""

    async def get(self) -> None:
        notebook_id = self.get_argument("notebook_id", None)
        status_str = self.get_argument("status", None)
        status = JobStatus(status_str) if status_str else None
        orch = await self.get_orch()
        jobs = await orch.list_jobs(notebook_id=notebook_id, status=status)
        self.write_json({
            "jobs": [
                {
                    "job_id": j.job_id,
                    "notebook_id": j.notebook_id,
                    "actor_id": j.actor_id,
                    "actor_type": j.actor_type.value,
                    "action": j.action.value,
                    "status": j.status.value,
                    "target_cells": j.target_cells,
                    "created_at": j.created_at,
                    "started_at": j.started_at,
                    "completed_at": j.completed_at,
                }
                for j in jobs
            ]
        })


class JobHandler(BaseHypernoteHandler):
    """GET: single job status."""

    async def get(self, job_id: str) -> None:
        orch = await self.get_orch()
        job = await orch.get_job(job_id)
        if job is None:
            raise tornado.web.HTTPError(404, reason=f"Job {job_id} not found")
        self.write_json({
            "job_id": job.job_id,
            "notebook_id": job.notebook_id,
            "actor_id": job.actor_id,
            "actor_type": job.actor_type.value,
            "action": job.action.value,
            "status": job.status.value,
            "target_cells": job.target_cells,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "runtime_id": job.runtime_id,
            "reconnect_ref": job.reconnect_ref,
        })


class SendStdinHandler(BaseHypernoteHandler):
    """POST: send stdin value for a job awaiting input."""

    async def post(self, job_id: str) -> None:
        body = self.get_json_body()
        value = body.get("value", "")
        actor_id, actor_type = self.get_actor()
        orch = await self.get_orch()
        try:
            await orch.send_stdin(job_id, value, actor_id, actor_type)
            self.write_json({"sent": True})
        except ValueError as e:
            raise tornado.web.HTTPError(400, reason=str(e))


# --- Runtime handlers ---


class RuntimeStatusHandler(BaseHypernoteHandler):
    """GET: runtime status for a notebook."""

    async def get(self, notebook_id: str) -> None:
        orch = await self.get_orch()
        status = await orch.get_runtime_status(notebook_id)
        self.write_json(status)


class RuntimeOpenHandler(BaseHypernoteHandler):
    """POST: open/attach a runtime for a notebook."""

    async def post(self, notebook_id: str) -> None:
        body = self.get_json_body()
        client_id = body.get("client_id", "api-client")
        orch = await self.get_orch()
        info = await orch.runtime_manager.open_runtime(notebook_id, client_id)
        self.write_json({
            "runtime_id": info.runtime_id,
            "state": info.state.value,
            "kernel_id": info.kernel_id,
        })


class RuntimeStopHandler(BaseHypernoteHandler):
    """POST: stop runtime for a notebook."""

    async def post(self, notebook_id: str) -> None:
        orch = await self.get_orch()
        runtime = orch.runtime_manager.get_runtime_for_notebook(notebook_id)
        if runtime is None:
            raise tornado.web.HTTPError(404, reason="No runtime for notebook")
        info = await orch.runtime_manager.stop_runtime(runtime.runtime_id)
        self.write_json({"runtime_id": info.runtime_id, "state": info.state.value})


class InterruptHandler(BaseHypernoteHandler):
    """POST: interrupt execution for a notebook."""

    async def post(self, notebook_id: str) -> None:
        actor_id, actor_type = self.get_actor()
        orch = await self.get_orch()
        try:
            await orch.interrupt(notebook_id, actor_id, actor_type)
            self.write_json({"interrupted": True})
        except ValueError as e:
            raise tornado.web.HTTPError(400, reason=str(e))
