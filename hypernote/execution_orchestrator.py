"""ExecutionOrchestrator: attributed execution over Jupyter primitives.

Thin wrapper that connects RuntimeManager + ActorLedger to an execution
backend (jupyter-server-nbmodel's ExecutionStack in production).
Adds identity and visibility to every execution action.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from hypernote.actor_ledger import ActorLedger, ActorType, Job, JobAction, JobStatus
from hypernote.runtime_manager import RuntimeManager, RuntimeState

logger = logging.getLogger(__name__)


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    OK = "ok"
    ERROR = "error"
    ABORTED = "aborted"
    AWAITING_INPUT = "awaiting_input"


@dataclass
class ExecutionResult:
    status: ExecutionStatus
    execution_count: int | None = None
    outputs: list[dict[str, Any]] | None = None
    error: str | None = None
    input_prompt: str | None = None


class ExecutionBackend:
    """Interface to the actual code execution layer.

    In production, wraps jupyter-server-nbmodel's ExecutionStack.
    """

    async def execute(self, kernel_id: str, code: str) -> str:
        """Submit code for execution. Returns a request UID for polling."""
        raise NotImplementedError

    async def poll_result(self, kernel_id: str, request_uid: str) -> ExecutionResult:
        """Poll for execution result."""
        raise NotImplementedError

    async def send_input(self, kernel_id: str, value: str) -> None:
        """Send stdin input to a running execution."""
        raise NotImplementedError


class NotebookBackend:
    """Interface to notebook document operations.

    In production, operates on the Y.js shared notebook model.
    """

    async def get_cell_source(self, notebook_id: str, cell_id: str) -> str:
        """Read source code from a cell."""
        raise NotImplementedError

    async def list_cells(self, notebook_id: str) -> list[dict[str, Any]]:
        """List all cells with their IDs, types, and sources."""
        raise NotImplementedError

    async def insert_cell(
        self, notebook_id: str, index: int, cell_type: str, source: str
    ) -> str:
        """Insert a cell, return its cell_id."""
        raise NotImplementedError

    async def replace_cell_source(
        self, notebook_id: str, cell_id: str, source: str
    ) -> None:
        """Replace the source of an existing cell."""
        raise NotImplementedError

    async def delete_cell(self, notebook_id: str, cell_id: str) -> None:
        """Delete a cell."""
        raise NotImplementedError

    async def create_notebook(self, path: str) -> str:
        """Create a new notebook at path, return notebook_id."""
        raise NotImplementedError

    async def open_notebook(self, path: str) -> str:
        """Open an existing notebook, return notebook_id."""
        raise NotImplementedError

    async def save_notebook(self, notebook_id: str) -> None:
        """Persist the notebook to disk."""
        raise NotImplementedError


class ExecutionOrchestrator:
    """Attributed execution orchestration.

    Routes all execution through one path: actor -> job -> runtime -> kernel.
    Integrates with ActorLedger for attribution and RuntimeManager for lifecycle.
    """

    def __init__(
        self,
        ledger: ActorLedger,
        runtime_mgr: RuntimeManager,
        execution_backend: ExecutionBackend,
        notebook_backend: NotebookBackend,
    ):
        self._ledger = ledger
        self._runtime_mgr = runtime_mgr
        self._exec = execution_backend
        self._notebook = notebook_backend

    @property
    def ledger(self) -> ActorLedger:
        return self._ledger

    @property
    def runtime_manager(self) -> RuntimeManager:
        return self._runtime_mgr

    @property
    def notebook_backend(self) -> NotebookBackend:
        return self._notebook

    async def queue_execution(
        self,
        notebook_id: str,
        cell_ids: list[str],
        actor_id: str,
        actor_type: ActorType,
    ) -> Job:
        """Queue cells for execution with actor attribution."""
        runtime = self._runtime_mgr.get_runtime_for_notebook(notebook_id)
        if runtime is None or not runtime.is_live:
            runtime = await self._runtime_mgr.open_runtime(notebook_id, f"exec-{actor_id}")

        job = await self._ledger.create_job(
            notebook_id=notebook_id,
            actor_id=actor_id,
            actor_type=actor_type,
            action=JobAction.EXECUTE,
            target_cells=json.dumps(cell_ids),
            runtime_id=runtime.runtime_id,
        )

        # Execute cells sequentially within the job
        asyncio.create_task(self._execute_job(job, runtime.kernel_id, cell_ids, actor_id, actor_type))
        return job

    async def _execute_job(
        self,
        job: Job,
        kernel_id: str,
        cell_ids: list[str],
        actor_id: str,
        actor_type: ActorType,
    ) -> None:
        """Execute all cells in a job sequentially."""
        await self._ledger.update_job_status(job.job_id, JobStatus.RUNNING)
        self._runtime_mgr.touch_activity(job.runtime_id)

        try:
            for cell_id in cell_ids:
                source = await self._notebook.get_cell_source(job.notebook_id, cell_id)
                request_uid = await self._exec.execute(kernel_id, source)

                # Poll until completion
                result = await self._poll_until_done(kernel_id, request_uid, job)

                # Record cell execution attribution
                await self._ledger.update_cell_attribution(
                    job.notebook_id,
                    cell_id,
                    executor_id=actor_id,
                    executor_type=actor_type,
                )

                if result.status == ExecutionStatus.ERROR:
                    await self._ledger.update_job_status(job.job_id, JobStatus.FAILED)
                    return

                if result.status == ExecutionStatus.ABORTED:
                    await self._ledger.update_job_status(job.job_id, JobStatus.INTERRUPTED)
                    return

            await self._ledger.update_job_status(job.job_id, JobStatus.SUCCEEDED)

        except Exception:
            logger.exception("Job %s failed", job.job_id)
            await self._ledger.update_job_status(job.job_id, JobStatus.FAILED)

        finally:
            self._runtime_mgr.touch_activity(job.runtime_id)

    async def _poll_until_done(
        self, kernel_id: str, request_uid: str, job: Job
    ) -> ExecutionResult:
        while True:
            result = await self._exec.poll_result(kernel_id, request_uid)

            if result.status == ExecutionStatus.AWAITING_INPUT:
                await self._ledger.update_job_status(job.job_id, JobStatus.AWAITING_INPUT)
                self._runtime_mgr.set_runtime_state(job.runtime_id, RuntimeState.AWAITING_INPUT)
                # Wait briefly then poll again (stdin will be sent via send_stdin)
                await asyncio.sleep(0.5)
                continue

            if result.status == ExecutionStatus.PENDING:
                await asyncio.sleep(0.1)
                continue

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
        """Send stdin input for a job awaiting input."""
        job = await self._ledger.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        if job.status != JobStatus.AWAITING_INPUT:
            raise ValueError(f"Job {job_id} is {job.status}, not awaiting input")

        runtime = self._runtime_mgr.get_runtime_for_notebook(job.notebook_id)
        if runtime is None or not runtime.kernel_id:
            raise ValueError(f"No live runtime for notebook {job.notebook_id}")

        await self._exec.send_input(runtime.kernel_id, value)

        # Record the stdin reply in the ledger
        await self._ledger.create_job(
            notebook_id=job.notebook_id,
            actor_id=actor_id,
            actor_type=actor_type,
            action=JobAction.STDIN_REPLY,
            runtime_id=runtime.runtime_id,
        )

        await self._ledger.update_job_status(job.job_id, JobStatus.RUNNING)
        self._runtime_mgr.set_runtime_state(runtime.runtime_id, RuntimeState.LIVE_ATTACHED)

    async def interrupt(
        self,
        notebook_id: str,
        actor_id: str,
        actor_type: ActorType,
    ) -> None:
        """Interrupt execution for a notebook."""
        runtime = self._runtime_mgr.get_runtime_for_notebook(notebook_id)
        if runtime is None:
            raise ValueError(f"No runtime for notebook {notebook_id}")

        await self._runtime_mgr.interrupt_runtime(runtime.runtime_id)

        await self._ledger.create_job(
            notebook_id=notebook_id,
            actor_id=actor_id,
            actor_type=actor_type,
            action=JobAction.INTERRUPT,
            runtime_id=runtime.runtime_id,
        )

    async def get_runtime_status(self, notebook_id: str) -> dict[str, Any]:
        """Get runtime status for a notebook."""
        runtime = self._runtime_mgr.get_runtime_for_notebook(notebook_id)
        if runtime is None:
            return {"state": "stopped", "runtime_id": None, "kernel_id": None}

        return {
            "state": runtime.state.value,
            "runtime_id": runtime.runtime_id,
            "kernel_id": runtime.kernel_id,
            "attached_clients": list(runtime.attached_clients),
            "last_activity": runtime.last_activity,
        }

    # --- Notebook operations (pass-through with attribution) ---

    async def create_notebook(self, path: str) -> str:
        return await self._notebook.create_notebook(path)

    async def open_notebook(self, path: str) -> str:
        return await self._notebook.open_notebook(path)

    async def list_cells(self, notebook_id: str) -> list[dict[str, Any]]:
        return await self._notebook.list_cells(notebook_id)

    async def insert_cell(
        self,
        notebook_id: str,
        index: int,
        cell_type: str,
        source: str,
        actor_id: str,
        actor_type: ActorType,
    ) -> str:
        cell_id = await self._notebook.insert_cell(notebook_id, index, cell_type, source)
        await self._ledger.update_cell_attribution(
            notebook_id, cell_id, editor_id=actor_id, editor_type=actor_type
        )
        return cell_id

    async def replace_cell_source(
        self,
        notebook_id: str,
        cell_id: str,
        source: str,
        actor_id: str,
        actor_type: ActorType,
    ) -> None:
        await self._notebook.replace_cell_source(notebook_id, cell_id, source)
        await self._ledger.update_cell_attribution(
            notebook_id, cell_id, editor_id=actor_id, editor_type=actor_type
        )

    async def delete_cell(self, notebook_id: str, cell_id: str) -> None:
        await self._notebook.delete_cell(notebook_id, cell_id)

    async def save_notebook(self, notebook_id: str) -> None:
        await self._notebook.save_notebook(notebook_id)
