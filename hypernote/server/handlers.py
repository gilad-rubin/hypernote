"""Minimal Hypernote REST handlers."""

from __future__ import annotations

import json
import urllib.parse
from http import HTTPStatus
from typing import Any, Awaitable, Callable

import tornado.web
from jupyter_server.base.handlers import APIHandler

from hypernote.actor_ledger import ActorType, JobStatus
from hypernote.execution_orchestrator import ExecutionOrchestrator
from hypernote.runtime_manager import RuntimeKernelMismatchError


class BaseHypernoteHandler(APIHandler):
    def initialize(self, get_orchestrator: Callable[[], Awaitable[ExecutionOrchestrator]]) -> None:
        self._get_orchestrator = get_orchestrator

    async def get_orch(self) -> ExecutionOrchestrator:
        return await self._get_orchestrator()

    def get_actor(self) -> tuple[str, ActorType]:
        actor_id = self.request.headers.get("X-Hypernote-Actor-Id", "anonymous")
        actor_type_str = self.request.headers.get("X-Hypernote-Actor-Type", "human")
        actor_type = (
            ActorType(actor_type_str)
            if actor_type_str in {"human", "agent"}
            else ActorType.HUMAN
        )
        return actor_id, actor_type

    def get_json_body(self) -> dict[str, Any]:
        try:
            return json.loads(self.request.body) if self.request.body else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def write_json(self, data: Any, status: int = 200) -> None:
        self.set_status(status)
        self.set_header("Content-Type", "application/json")
        self.finish(json.dumps(data, default=str))

    @staticmethod
    def decode_notebook_id(notebook_id: str) -> str:
        return urllib.parse.unquote(notebook_id)


class ExecuteHandler(BaseHypernoteHandler):
    @tornado.web.authenticated
    async def post(self, notebook_id: str) -> None:
        notebook_id = self.decode_notebook_id(notebook_id)
        body = self.get_json_body()
        cell_ids = body.get("cell_ids", [])
        if not cell_ids:
            raise tornado.web.HTTPError(400, reason="cell_ids required")
        actor_id, actor_type = self.get_actor()
        orch = await self.get_orch()
        try:
            job = await orch.queue_execution(
                notebook_id,
                cell_ids,
                actor_id,
                actor_type,
                kernel_name=body.get("kernel_name"),
            )
        except RuntimeKernelMismatchError as exc:
            raise tornado.web.HTTPError(409, reason=str(exc)) from exc
        except Exception as exc:
            raise tornado.web.HTTPError(400, reason=str(exc) or exc.__class__.__name__) from exc
        self.write_json(
            {
                "job_id": job.job_id,
                "status": job.status.value,
                "notebook_id": notebook_id,
                "request_uids": job.request_uids,
            },
            status=HTTPStatus.ACCEPTED,
        )


class NotebookDocumentHandler(BaseHypernoteHandler):
    @tornado.web.authenticated
    async def get(self, notebook_id: str) -> None:
        notebook_id = self.decode_notebook_id(notebook_id)
        content = self.get_argument("content", "1") != "0"
        orch = await self.get_orch()
        try:
            model = await orch.notebook_accessor.get_notebook_model(notebook_id, content=content)
        except Exception as exc:
            raise tornado.web.HTTPError(404, reason=str(exc)) from exc
        self.write_json(model)

    @tornado.web.authenticated
    async def put(self, notebook_id: str) -> None:
        notebook_id = self.decode_notebook_id(notebook_id)
        orch = await self.get_orch()
        body = self.get_json_body()
        try:
            model = await orch.notebook_accessor.create_notebook(notebook_id, body)
        except Exception as exc:
            raise tornado.web.HTTPError(400, reason=str(exc)) from exc
        self.write_json(model)


class NotebookCellsHandler(BaseHypernoteHandler):
    @tornado.web.authenticated
    async def get(self, notebook_id: str) -> None:
        notebook_id = self.decode_notebook_id(notebook_id)
        orch = await self.get_orch()
        cells = await orch.notebook_accessor.list_cells(notebook_id)
        self.write_json({"cells": cells})

    @tornado.web.authenticated
    async def post(self, notebook_id: str) -> None:
        notebook_id = self.decode_notebook_id(notebook_id)
        body = self.get_json_body()
        actor_id, actor_type = self.get_actor()
        cell_type = body.get("cell_type", "code")
        cell_id = body.get("id")
        source = body.get("source", "")
        cell = {
            "id": cell_id,
            "cell_type": cell_type,
            "execution_count": None,
            "metadata": body.get("metadata", {}),
            "outputs": [],
            "source": source,
        }
        orch = await self.get_orch()
        try:
            created = await orch.notebook_accessor.insert_cell(
                notebook_id,
                cell,
                before=body.get("before"),
                after=body.get("after"),
            )
        except ValueError as exc:
            raise tornado.web.HTTPError(400, reason=str(exc)) from exc
        await orch.ledger.update_cell_attribution(
            notebook_id,
            created["id"],
            editor_id=actor_id,
            editor_type=actor_type,
        )
        self.write_json({"cell": created}, status=HTTPStatus.CREATED)


class NotebookCellHandler(BaseHypernoteHandler):
    @tornado.web.authenticated
    async def get(self, notebook_id: str, cell_id: str) -> None:
        notebook_id = self.decode_notebook_id(notebook_id)
        orch = await self.get_orch()
        try:
            cell = await orch.notebook_accessor.get_cell(notebook_id, cell_id)
        except ValueError as exc:
            raise tornado.web.HTTPError(404, reason=str(exc)) from exc
        self.write_json({"cell": cell})

    @tornado.web.authenticated
    async def patch(self, notebook_id: str, cell_id: str) -> None:
        notebook_id = self.decode_notebook_id(notebook_id)
        actor_id, actor_type = self.get_actor()
        body = self.get_json_body()
        if "source" not in body:
            raise tornado.web.HTTPError(400, reason="source required")
        orch = await self.get_orch()
        try:
            cell = await orch.notebook_accessor.replace_cell_source(
                notebook_id,
                cell_id,
                body["source"],
            )
        except ValueError as exc:
            raise tornado.web.HTTPError(400, reason=str(exc)) from exc
        await orch.ledger.update_cell_attribution(
            notebook_id,
            cell_id,
            editor_id=actor_id,
            editor_type=actor_type,
        )
        self.write_json({"cell": cell})

    @tornado.web.authenticated
    async def delete(self, notebook_id: str, cell_id: str) -> None:
        notebook_id = self.decode_notebook_id(notebook_id)
        orch = await self.get_orch()
        try:
            await orch.notebook_accessor.delete_cell(notebook_id, cell_id)
        except ValueError as exc:
            raise tornado.web.HTTPError(404, reason=str(exc)) from exc
        self.write_json({"deleted": True})


class NotebookCellMoveHandler(BaseHypernoteHandler):
    @tornado.web.authenticated
    async def post(self, notebook_id: str, cell_id: str) -> None:
        notebook_id = self.decode_notebook_id(notebook_id)
        actor_id, actor_type = self.get_actor()
        body = self.get_json_body()
        orch = await self.get_orch()
        try:
            cell = await orch.notebook_accessor.move_cell(
                notebook_id,
                cell_id,
                before=body.get("before"),
                after=body.get("after"),
            )
        except ValueError as exc:
            raise tornado.web.HTTPError(400, reason=str(exc)) from exc
        await orch.ledger.update_cell_attribution(
            notebook_id,
            cell_id,
            editor_id=actor_id,
            editor_type=actor_type,
        )
        self.write_json({"cell": cell})


class NotebookCellClearOutputsHandler(BaseHypernoteHandler):
    @tornado.web.authenticated
    async def post(self, notebook_id: str, cell_id: str) -> None:
        notebook_id = self.decode_notebook_id(notebook_id)
        actor_id, actor_type = self.get_actor()
        orch = await self.get_orch()
        try:
            cell = await orch.notebook_accessor.clear_outputs(notebook_id, cell_id)
        except ValueError as exc:
            raise tornado.web.HTTPError(404, reason=str(exc)) from exc
        await orch.ledger.update_cell_attribution(
            notebook_id,
            cell_id,
            editor_id=actor_id,
            editor_type=actor_type,
        )
        self.write_json({"cell": cell})


class JobsHandler(BaseHypernoteHandler):
    @tornado.web.authenticated
    async def get(self) -> None:
        notebook_id = self.get_argument("notebook_id", None)
        if notebook_id is not None:
            notebook_id = self.decode_notebook_id(notebook_id)
        status_str = self.get_argument("status", None)
        status = JobStatus(status_str) if status_str else None
        orch = await self.get_orch()
        jobs = await orch.list_jobs(notebook_id=notebook_id, status=status)
        self.write_json(
            {
                "jobs": [
                    {
                        "job_id": job.job_id,
                        "notebook_id": job.notebook_id,
                        "actor_id": job.actor_id,
                        "actor_type": job.actor_type.value,
                        "action": job.action.value,
                        "status": job.status.value,
                        "target_cells": job.target_cells,
                        "request_uids": job.request_uids,
                        "created_at": job.created_at,
                        "started_at": job.started_at,
                        "completed_at": job.completed_at,
                        "runtime_id": job.runtime_id,
                    }
                    for job in jobs
                ]
            }
        )


class JobHandler(BaseHypernoteHandler):
    @tornado.web.authenticated
    async def get(self, job_id: str) -> None:
        orch = await self.get_orch()
        job = await orch.get_job(job_id)
        if job is None:
            raise tornado.web.HTTPError(404, reason=f"Job {job_id} not found")
        self.write_json(
            {
                "job_id": job.job_id,
                "notebook_id": job.notebook_id,
                "actor_id": job.actor_id,
                "actor_type": job.actor_type.value,
                "action": job.action.value,
                "status": job.status.value,
                "target_cells": job.target_cells,
                "request_uids": job.request_uids,
                "created_at": job.created_at,
                "started_at": job.started_at,
                "completed_at": job.completed_at,
                "runtime_id": job.runtime_id,
            }
        )


class SendStdinHandler(BaseHypernoteHandler):
    @tornado.web.authenticated
    async def post(self, job_id: str) -> None:
        body = self.get_json_body()
        value = body.get("value", "")
        actor_id, actor_type = self.get_actor()
        orch = await self.get_orch()
        try:
            await orch.send_stdin(job_id, value, actor_id, actor_type)
            self.write_json({"sent": True})
        except ValueError as exc:
            raise tornado.web.HTTPError(400, reason=str(exc)) from exc


class RuntimeStatusHandler(BaseHypernoteHandler):
    @tornado.web.authenticated
    async def get(self, notebook_id: str) -> None:
        notebook_id = self.decode_notebook_id(notebook_id)
        orch = await self.get_orch()
        runtime = await orch.get_runtime_status(notebook_id)
        active_jobs = await orch.list_active_jobs(notebook_id)
        runtime["jobs"] = [job.job_id for job in active_jobs]
        self.write_json(runtime)


class RuntimeOpenHandler(BaseHypernoteHandler):
    @tornado.web.authenticated
    async def post(self, notebook_id: str) -> None:
        notebook_id = self.decode_notebook_id(notebook_id)
        body = self.get_json_body()
        client_id = body.get("client_id", "api-client")
        orch = await self.get_orch()
        kernel_name = await orch.resolve_kernel_name(
            notebook_id,
            explicit_kernel_name=body.get("kernel_name"),
        )
        try:
            room = await orch.runtime_manager.open_runtime(
                notebook_id,
                client_id,
                kernel_name=kernel_name,
            )
        except RuntimeKernelMismatchError as exc:
            raise tornado.web.HTTPError(409, reason=str(exc)) from exc
        except Exception as exc:
            raise tornado.web.HTTPError(400, reason=str(exc) or exc.__class__.__name__) from exc
        self.write_json(
            {
                "room_id": room.room_id,
                "state": room.state.value,
                "session_id": room.session_id,
                "kernel_id": room.kernel_id,
                "kernel_name": room.kernel_name,
                "attached_clients": sorted(room.attached_clients),
            }
        )


class RuntimeStopHandler(BaseHypernoteHandler):
    @tornado.web.authenticated
    async def post(self, notebook_id: str) -> None:
        notebook_id = self.decode_notebook_id(notebook_id)
        orch = await self.get_orch()
        room = orch.runtime_manager.get_room_for_notebook(notebook_id)
        if room is None:
            raise tornado.web.HTTPError(404, reason="No runtime for notebook")
        room = await orch.runtime_manager.stop_runtime(room.room_id)
        self.write_json({"room_id": room.room_id, "state": room.state.value})


class InterruptHandler(BaseHypernoteHandler):
    @tornado.web.authenticated
    async def post(self, notebook_id: str) -> None:
        notebook_id = self.decode_notebook_id(notebook_id)
        actor_id, actor_type = self.get_actor()
        orch = await self.get_orch()
        try:
            await orch.interrupt(notebook_id, actor_id, actor_type)
            self.write_json({"interrupted": True})
        except ValueError as exc:
            raise tornado.web.HTTPError(400, reason=str(exc)) from exc


class CellAttributionHandler(BaseHypernoteHandler):
    @tornado.web.authenticated
    async def get(self, notebook_id: str, cell_id: str) -> None:
        notebook_id = self.decode_notebook_id(notebook_id)
        orch = await self.get_orch()
        attr = await orch.ledger.get_cell_attribution(notebook_id, cell_id)
        if attr is None:
            self.write_json({})
            return
        self.write_json(
            {
                "last_editor_id": attr.last_editor_id,
                "last_editor_type": attr.last_editor_type,
                "last_executor_id": attr.last_executor_id,
                "last_executor_type": attr.last_executor_type,
                "updated_at": attr.updated_at,
            }
        )
