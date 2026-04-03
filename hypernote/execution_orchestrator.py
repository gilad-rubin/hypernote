"""Thin job service over Jupyter's ExecutionStack.

Hypernote does not own notebook execution semantics. It owns parent-job
tracking, actor attribution, and room-aware lifecycle coordination around
Jupyter's server-side execution path.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import PurePosixPath
from typing import Any

from jupyter_core.utils import ensure_async
from jupyter_server_ydoc.rooms import DocumentRoom
from jupyter_server_ydoc.utils import encode_file_path, room_id_from_encoded_path
from pycrdt import Map

from hypernote.actor_ledger import ActorType, Job, JobAction, JobStatus, Ledger
from hypernote.runtime_manager import RuntimeManager, RuntimeState

logger = logging.getLogger(__name__)


class SharedNotebookAccessor:
    """Read and mutate notebook state through the shared YDoc model when available."""

    def __init__(self, ydoc_extension: Any | None, contents_manager: Any):
        self._ydoc_extension = ydoc_extension
        self._contents_manager = contents_manager

    async def get_notebook_model(
        self,
        notebook_id: str,
        *,
        content: bool = True,
    ) -> dict[str, Any]:
        if self._ydoc_extension is None:
            return await ensure_async(self._contents_manager.get(notebook_id, content=content))

        if not content:
            return await ensure_async(self._contents_manager.get(notebook_id, content=False))

        await ensure_async(self._contents_manager.get(notebook_id, content=False))

        room = await self.get_document_room(notebook_id)
        model: dict[str, Any] = {"path": notebook_id, "type": "notebook"}
        model["content"] = await room._document.aget()
        return model

    async def create_notebook(self, notebook_id: str, model: dict[str, Any]) -> dict[str, Any]:
        dir_path = str(PurePosixPath(notebook_id).parent)
        created = await ensure_async(
            self._contents_manager.new_untitled(
                path="" if dir_path == "." else dir_path,
                type="notebook",
                ext=".ipynb",
            )
        )
        if created["path"] != notebook_id:
            created = await ensure_async(
                self._contents_manager.rename_file(created["path"], notebook_id)
            )
        saved = await ensure_async(self._contents_manager.save(model, notebook_id))
        if self._ydoc_extension is not None:
            room = await self.get_document_room(notebook_id)
            await room.initialize()
            await self._save_room(room)
            return await self.get_notebook_model(notebook_id, content=True)
        return saved

    async def get_cell_source(self, notebook_id: str, cell_id: str) -> str:
        await self.ensure_document_room(notebook_id)
        ycell = await self.get_ycell(notebook_id, cell_id)
        if ycell is not None:
            source = ycell["source"]
            return source.to_py() if hasattr(source, "to_py") else str(source)

        model = await ensure_async(self._contents_manager.get(notebook_id, content=True))
        for cell in model["content"].get("cells", []):
            if cell.get("id") == cell_id:
                source = cell.get("source", "")
                if isinstance(source, list):
                    return "".join(source)
                return source
        raise ValueError(f"Cell {cell_id} not found in notebook {notebook_id}")

    async def get_ycell(self, notebook_id: str, cell_id: str) -> Map | None:
        if self._ydoc_extension is None:
            return None

        room = await self.get_document_room(notebook_id)
        notebook = room._document

        matches = [ycell for ycell in notebook.ycells if ycell["id"] == cell_id]
        if not matches:
            return None
        if len(matches) > 1:
            logger.warning("Multiple cells found for %s in %s; using first", cell_id, notebook_id)
        return matches[0]

    async def list_cells(self, notebook_id: str) -> list[dict[str, Any]]:
        model = await self.get_notebook_model(notebook_id, content=True)
        cells = []
        for idx, cell in enumerate(model["content"].get("cells", [])):
            cells.append(
                {
                    "id": cell.get("id", f"cell-{idx}"),
                    "type": cell.get("cell_type", "code"),
                    "source": _cell_source(cell),
                    "index": idx,
                    "execution_count": cell.get("execution_count"),
                    "outputs": list(cell.get("outputs", [])),
                    "metadata": cell.get("metadata", {}),
                }
            )
        return cells

    async def get_cell(self, notebook_id: str, cell_id: str) -> dict[str, Any]:
        model = await self.get_notebook_model(notebook_id, content=True)
        for cell in model["content"].get("cells", []):
            if cell.get("id") == cell_id:
                return cell
        raise ValueError(f"Cell {cell_id} not found in notebook {notebook_id}")

    async def get_kernelspec_name(self, notebook_id: str) -> str:
        model = await self.get_notebook_model(notebook_id, content=True)
        metadata = model.get("content", {}).get("metadata", {})
        kernelspec = metadata.get("kernelspec", {})
        return str(kernelspec.get("name") or "python3")

    async def insert_cell(
        self,
        notebook_id: str,
        cell: dict[str, Any],
        *,
        before: str | None = None,
        after: str | None = None,
    ) -> dict[str, Any]:
        _validate_position(before=before, after=after)
        if self._ydoc_extension is None:
            model = await ensure_async(self._contents_manager.get(notebook_id, content=True))
            cells = model["content"].get("cells", [])
            index = _resolve_insert_index(cells, before=before, after=after)
            cells.insert(index, cell)
            model["content"]["cells"] = _assign_position_keys(cells)
            await ensure_async(self._contents_manager.save(model, notebook_id))
            return model["content"]["cells"][index]

        room = await self.get_document_room(notebook_id)
        notebook = room._document
        index = _resolve_insert_index(
            [notebook.get_cell(i) for i in range(len(notebook.ycells))],
            before=before,
            after=after,
        )
        ycell = notebook.create_ycell(cell)
        if index >= len(notebook.ycells):
            notebook.ycells.append(ycell)
        else:
            notebook.ycells.insert(index, ycell)
        _assign_position_keys_ycells(notebook.ycells)
        await self._save_room(room)
        return await self.get_cell(notebook_id, cell["id"])

    async def replace_cell_source(
        self,
        notebook_id: str,
        cell_id: str,
        source: str,
    ) -> dict[str, Any]:
        if self._ydoc_extension is None:
            model = await ensure_async(self._contents_manager.get(notebook_id, content=True))
            cells = model["content"].get("cells", [])
            index = _find_cell_index(cells, cell_id)
            cells[index]["source"] = source
            model["content"]["cells"] = _assign_position_keys(cells)
            await ensure_async(self._contents_manager.save(model, notebook_id))
            return cells[index]

        room = await self.get_document_room(notebook_id)
        notebook = room._document
        index = _find_ycell_index(notebook.ycells, cell_id)
        cell = notebook.get_cell(index)
        cell["source"] = source
        notebook.set_cell(index, cell)
        _assign_position_keys_ycells(notebook.ycells)
        await self._save_room(room)
        return await self.get_cell(notebook_id, cell_id)

    async def delete_cell(self, notebook_id: str, cell_id: str) -> None:
        if self._ydoc_extension is None:
            model = await ensure_async(self._contents_manager.get(notebook_id, content=True))
            cells = model["content"].get("cells", [])
            index = _find_cell_index(cells, cell_id)
            cells.pop(index)
            model["content"]["cells"] = _assign_position_keys(cells)
            await ensure_async(self._contents_manager.save(model, notebook_id))
            return

        room = await self.get_document_room(notebook_id)
        notebook = room._document
        index = _find_ycell_index(notebook.ycells, cell_id)
        notebook.ycells.pop(index)
        _assign_position_keys_ycells(notebook.ycells)
        await self._save_room(room)

    async def move_cell(
        self,
        notebook_id: str,
        cell_id: str,
        *,
        before: str | None = None,
        after: str | None = None,
    ) -> dict[str, Any]:
        _validate_position(before=before, after=after)
        if self._ydoc_extension is None:
            model = await ensure_async(self._contents_manager.get(notebook_id, content=True))
            cells = model["content"].get("cells", [])
            index = _find_cell_index(cells, cell_id)
            cell = cells.pop(index)
            target = _resolve_insert_index(cells, before=before, after=after)
            cells.insert(target, cell)
            model["content"]["cells"] = _assign_position_keys(cells)
            await ensure_async(self._contents_manager.save(model, notebook_id))
            return cell

        room = await self.get_document_room(notebook_id)
        notebook = room._document
        cells = [notebook.get_cell(i) for i in range(len(notebook.ycells))]
        index = _find_cell_index(cells, cell_id)
        cell = cells.pop(index)
        target = _resolve_insert_index(cells, before=before, after=after)
        cells.insert(target, cell)
        await notebook.aset(
            {
                "cells": _assign_position_keys(cells),
                "metadata": notebook.get()["metadata"],
                "nbformat": notebook.get()["nbformat"],
                "nbformat_minor": notebook.get()["nbformat_minor"],
            }
        )
        await self._save_room(room)
        return await self.get_cell(notebook_id, cell_id)

    async def clear_outputs(self, notebook_id: str, cell_id: str) -> dict[str, Any]:
        if self._ydoc_extension is None:
            model = await ensure_async(self._contents_manager.get(notebook_id, content=True))
            cells = model["content"].get("cells", [])
            index = _find_cell_index(cells, cell_id)
            cells[index]["outputs"] = []
            cells[index]["execution_count"] = None
            model["content"]["cells"] = _assign_position_keys(cells)
            await ensure_async(self._contents_manager.save(model, notebook_id))
            return cells[index]

        room = await self.get_document_room(notebook_id)
        notebook = room._document
        index = _find_ycell_index(notebook.ycells, cell_id)
        cell = notebook.get_cell(index)
        cell["outputs"] = []
        cell["execution_count"] = None
        notebook.set_cell(index, cell)
        _assign_position_keys_ycells(notebook.ycells)
        await self._save_room(room)
        return await self.get_cell(notebook_id, cell_id)

    async def flush_document(self, notebook_id: str) -> None:
        if self._ydoc_extension is None:
            return
        room = await self.get_document_room(notebook_id)
        await self._save_room(room)

    async def ensure_document_room(self, notebook_id: str) -> str:
        if self._ydoc_extension is None:
            return notebook_id

        room = await self.get_document_room(notebook_id)
        return room.room_id

    async def get_document_room(self, notebook_id: str) -> DocumentRoom:
        if self._ydoc_extension is None:
            raise RuntimeError("Shared document rooms are unavailable without jupyter_server_ydoc")

        file_id_manager = self._ydoc_extension.serverapp.web_app.settings["file_id_manager"]
        file_id = file_id_manager.index(notebook_id)
        encoded = encode_file_path("json", "notebook", file_id)
        room_id = room_id_from_encoded_path(encoded)
        server = self._ydoc_extension.ywebsocket_server
        if not server.started.is_set():
            asyncio.create_task(server.start())
            await server.started.wait()
        if server.room_exists(room_id):
            room = await server.get_room(room_id)
            if isinstance(room, DocumentRoom):
                await room.initialize()
                return room
            raise RuntimeError(f"Room {room_id} is not a DocumentRoom")

        file_loader = self._ydoc_extension.file_loaders[file_id]
        updates_file_path = f".notebook:{file_id}.y"
        ystore = server.ystore_class(path=updates_file_path, log=self._ydoc_extension.log)
        room = DocumentRoom(
            room_id,
            "json",
            "notebook",
            file_loader,
            self._ydoc_extension.serverapp.event_logger,
            ystore,
            self._ydoc_extension.log,
            save_delay=self._ydoc_extension.document_save_delay,
        )
        await server.start_room(room)
        server.add_room(room_id, room)
        await room.initialize()
        return room

    async def _save_room(self, room: DocumentRoom) -> None:
        save_task = room._save_to_disc()
        if save_task is not None:
            await save_task


class ExecutionOrchestrator:
    """Hypernote job service over Jupyter's ExecutionStack."""

    def __init__(
        self,
        ledger: Ledger,
        runtime_mgr: RuntimeManager,
        execution_stack: Any,
        notebook_accessor: SharedNotebookAccessor,
    ):
        self._ledger = ledger
        self._runtime_mgr = runtime_mgr
        self._stack = execution_stack
        self._notebook = notebook_accessor
        self._awaiting_input_signatures: dict[str, str] = {}
        self._resumed_input_signatures: dict[str, str] = {}

    @property
    def ledger(self) -> Ledger:
        return self._ledger

    @property
    def runtime_manager(self) -> RuntimeManager:
        return self._runtime_mgr

    @property
    def notebook_accessor(self) -> SharedNotebookAccessor:
        return self._notebook

    async def queue_execution(
        self,
        notebook_id: str,
        cell_ids: list[str],
        actor_id: str,
        actor_type: ActorType,
        *,
        kernel_name: str | None = None,
    ) -> Job:
        desired_kernel_name = await self.resolve_kernel_name(
            notebook_id,
            explicit_kernel_name=kernel_name,
        )
        room = await self._runtime_mgr.ensure_room(
            notebook_id,
            kernel_name=desired_kernel_name,
        )
        job = await self._ledger.create_job(
            notebook_id=notebook_id,
            actor_id=actor_id,
            actor_type=actor_type,
            action=JobAction.EXECUTE,
            target_cells=json.dumps(cell_ids),
            runtime_id=room.room_id,
        )
        self._runtime_mgr.mark_job_started(notebook_id, job.job_id)
        asyncio.create_task(self._run_job(job, cell_ids, actor_id, actor_type))
        return job

    async def _run_job(
        self,
        job: Job,
        cell_ids: list[str],
        actor_id: str,
        actor_type: ActorType,
    ) -> None:
        room = await self._runtime_mgr.ensure_room(job.notebook_id)
        async with room.job_lock:
            await self._ledger.update_job_status(
                job.job_id,
                JobStatus.RUNNING,
                runtime_id=room.room_id,
            )
            self._runtime_mgr.touch_activity(room.room_id)

            try:
                for cell_id in cell_ids:
                    await self._ensure_kernel_client_ready(room.kernel_id)
                    source = await self._notebook.get_cell_source(job.notebook_id, cell_id)
                    document_id = await self._notebook.ensure_document_room(job.notebook_id)
                    request_uid = self._stack.put(
                        room.kernel_id,
                        source,
                        {"document_id": document_id, "cell_id": cell_id},
                    )
                    await self._ledger.append_request_uid(job.job_id, request_uid)

                    result = await self._poll_request(
                        room.room_id,
                        room.kernel_id,
                        request_uid,
                        job.job_id,
                    )
                    await self._notebook.flush_document(job.notebook_id)

                    await self._ledger.update_cell_attribution(
                        job.notebook_id,
                        cell_id,
                        executor_id=actor_id,
                        executor_type=actor_type,
                    )

                    if "error" in result or result.get("status") == "error":
                        await self._ledger.update_job_status(job.job_id, JobStatus.FAILED)
                        return

                await self._ledger.update_job_status(job.job_id, JobStatus.SUCCEEDED)
            except Exception:
                logger.exception("Job %s failed", job.job_id)
                await self._ledger.update_job_status(job.job_id, JobStatus.FAILED)
            finally:
                self._runtime_mgr.mark_job_finished(job.notebook_id, job.job_id)
                if room.state == RuntimeState.AWAITING_INPUT:
                    self._runtime_mgr.set_runtime_state(room.room_id, RuntimeState.LIVE_DETACHED)
                else:
                    self._runtime_mgr.touch_activity(room.room_id)

    async def _poll_request(
        self,
        room_id: str,
        kernel_id: str,
        request_uid: str,
        job_id: str,
    ) -> dict[str, Any]:
        while True:
            result = self._stack.get(kernel_id, request_uid)
            if result is None:
                await asyncio.sleep(0.1)
                continue
            input_request = result.get("input_request")
            if input_request is not None:
                signature = _input_request_signature(input_request)
                self._awaiting_input_signatures[job_id] = signature
                if self._resumed_input_signatures.get(job_id) != signature:
                    await self._ledger.update_job_status(job_id, JobStatus.AWAITING_INPUT)
                    self._runtime_mgr.set_runtime_state(room_id, RuntimeState.AWAITING_INPUT)
                await asyncio.sleep(0.25)
                continue

            self._awaiting_input_signatures.pop(job_id, None)
            self._resumed_input_signatures.pop(job_id, None)
            self._runtime_mgr.touch_activity(room_id)
            return result

    async def get_job(self, job_id: str) -> Job | None:
        return await self._ledger.get_job(job_id)

    async def list_jobs(
        self,
        notebook_id: str | None = None,
        status: JobStatus | None = None,
    ) -> list[Job]:
        return await self._ledger.list_jobs(notebook_id=notebook_id, status=status)

    async def list_active_jobs(self, notebook_id: str) -> list[Job]:
        return await self._ledger.list_active_jobs(notebook_id)

    async def send_stdin(
        self,
        job_id: str,
        value: str,
        actor_id: str,
        actor_type: ActorType,
    ) -> None:
        job = await self._ledger.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        if job.status != JobStatus.AWAITING_INPUT:
            raise ValueError(f"Job {job_id} is {job.status}, not awaiting input")

        room = await self._runtime_mgr.ensure_room(job.notebook_id)
        if room.kernel_id is None:
            raise ValueError(f"Notebook {job.notebook_id} has no live runtime")

        await self._stack.send_input(room.kernel_id, value)
        awaiting_signature = self._awaiting_input_signatures.get(job.job_id)
        if awaiting_signature is not None:
            self._resumed_input_signatures[job.job_id] = awaiting_signature
        await self._ledger.create_job(
            notebook_id=job.notebook_id,
            actor_id=actor_id,
            actor_type=actor_type,
            action=JobAction.STDIN_REPLY,
            runtime_id=room.room_id,
        )
        await self._ledger.update_job_status(job.job_id, JobStatus.RUNNING, runtime_id=room.room_id)
        next_state = (
            RuntimeState.LIVE_ATTACHED
            if room.attached_clients
            else RuntimeState.LIVE_DETACHED
        )
        self._runtime_mgr.set_runtime_state(room.room_id, next_state)

    async def interrupt(
        self,
        notebook_id: str,
        actor_id: str,
        actor_type: ActorType,
    ) -> None:
        room = await self._runtime_mgr.ensure_room(notebook_id)
        await self._runtime_mgr.interrupt_runtime(room.room_id)
        await self._ledger.create_job(
            notebook_id=notebook_id,
            actor_id=actor_id,
            actor_type=actor_type,
            action=JobAction.INTERRUPT,
            runtime_id=room.room_id,
        )

    async def get_runtime_status(self, notebook_id: str) -> dict[str, Any]:
        return await self._runtime_mgr.get_runtime_status(notebook_id)

    async def resolve_kernel_name(
        self,
        notebook_id: str,
        *,
        explicit_kernel_name: str | None = None,
    ) -> str:
        if explicit_kernel_name:
            return explicit_kernel_name
        return await self._notebook.get_kernelspec_name(notebook_id)

    async def _ensure_kernel_client_ready(self, kernel_id: str) -> None:
        get_client = getattr(self._stack, "_get_client", None)
        if get_client is None:
            return

        client = get_client(kernel_id)
        try:
            client.start_channels()
        except RuntimeError:
            pass

        wait_for_ready = getattr(client, "wait_for_ready", None)
        if wait_for_ready is not None:
            await ensure_async(wait_for_ready(timeout=30))

    async def list_cells(self, notebook_id: str) -> list[dict[str, Any]]:
        return await self._notebook.list_cells(notebook_id)

    async def create_notebook(self, path: str) -> str:
        dir_path = str(PurePosixPath(path).parent)
        model = await ensure_async(
            self._notebook._contents_manager.new_untitled(
                path="" if dir_path == "." else dir_path,
                type="notebook",
                ext=".ipynb",
            )
        )
        if model["path"] != path:
            model = await ensure_async(
                self._notebook._contents_manager.rename_file(model["path"], path)
            )
        return model["path"]

    async def open_notebook(self, path: str) -> str:
        model = await ensure_async(self._notebook._contents_manager.get(path, content=False))
        return model["path"]


def _cell_source(cell: dict[str, Any]) -> str:
    source = cell.get("source", "")
    if isinstance(source, list):
        return "".join(source)
    return str(source)


def _input_request_signature(input_request: Any) -> str:
    return json.dumps(input_request, sort_keys=True, default=str)


def _validate_position(*, before: str | None, after: str | None) -> None:
    if before is not None and after is not None:
        raise ValueError("Specify only one of before= or after=")


def _resolve_insert_index(
    cells: list[dict[str, Any]],
    *,
    before: str | None,
    after: str | None,
) -> int:
    if before is None and after is None:
        return len(cells)
    if before is not None:
        return _find_cell_index(cells, before)
    return _find_cell_index(cells, after) + 1


def _find_cell_index(cells: list[dict[str, Any]], cell_id: str) -> int:
    for index, cell in enumerate(cells):
        if cell.get("id") == cell_id:
            return index
    raise ValueError(f"Cell {cell_id} not found")


def _find_ycell_index(ycells: Any, cell_id: str) -> int:
    for index, ycell in enumerate(ycells):
        if ycell["id"] == cell_id:
            return index
    raise ValueError(f"Cell {cell_id} not found")


def _assign_position_keys(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for index, cell in enumerate(cells):
        metadata = dict(cell.get("metadata") or {})
        hypernote_meta = dict(metadata.get("hypernote") or {})
        hypernote_meta["position_key"] = f"{index:09d}"
        metadata["hypernote"] = hypernote_meta
        cell["metadata"] = metadata
    return cells


def _assign_position_keys_ycells(ycells: Any) -> None:
    for index, ycell in enumerate(ycells):
        metadata = ycell.get("metadata")
        if not isinstance(metadata, Map):
            metadata = Map(metadata or {})
            ycell["metadata"] = metadata
        hypernote_meta = metadata.get("hypernote")
        if not isinstance(hypernote_meta, Map):
            hypernote_meta = Map(hypernote_meta or {})
            metadata["hypernote"] = hypernote_meta
        hypernote_meta["position_key"] = f"{index:09d}"
