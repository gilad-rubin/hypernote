"""Ephemeral ledger for job tracking and cell attribution metadata.

Hypernote treats runtimes as server-owned but notebook-scoped and ephemeral.
This ledger mirrors that model: it keeps recent job state and attribution in
memory, and callers can evict a notebook's state when its runtime is stopped.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class ActorType(str, Enum):
    HUMAN = "human"
    AGENT = "agent"


class JobAction(str, Enum):
    EXECUTE = "execute"
    INTERRUPT = "interrupt"
    STDIN_REPLY = "stdin_reply"


@dataclass
class Job:
    job_id: str
    notebook_id: str
    actor_id: str
    actor_type: ActorType
    action: JobAction
    status: JobStatus
    created_at: float
    runtime_id: str | None = None
    target_cells: str | None = None
    request_uids: list[str] = field(default_factory=list)
    started_at: float | None = None
    completed_at: float | None = None


@dataclass
class CellAttribution:
    notebook_id: str
    cell_id: str
    updated_at: float
    last_editor_id: str | None = None
    last_editor_type: str | None = None
    last_executor_id: str | None = None
    last_executor_type: str | None = None


class Ledger(Protocol):
    async def initialize(self) -> None: ...

    async def close(self) -> None: ...

    async def create_job(
        self,
        notebook_id: str,
        actor_id: str,
        actor_type: ActorType,
        action: JobAction,
        target_cells: str | None = None,
        runtime_id: str | None = None,
    ) -> Job: ...

    async def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        runtime_id: str | None = None,
    ) -> None: ...

    async def append_request_uid(self, job_id: str, request_uid: str) -> None: ...

    async def get_job(self, job_id: str) -> Job | None: ...

    async def list_jobs(
        self,
        notebook_id: str | None = None,
        status: JobStatus | None = None,
        limit: int = 100,
    ) -> list[Job]: ...

    async def list_active_jobs(self, notebook_id: str) -> list[Job]: ...

    async def update_cell_attribution(
        self,
        notebook_id: str,
        cell_id: str,
        editor_id: str | None = None,
        editor_type: ActorType | None = None,
        executor_id: str | None = None,
        executor_type: ActorType | None = None,
    ) -> None: ...

    async def get_cell_attribution(
        self,
        notebook_id: str,
        cell_id: str,
    ) -> CellAttribution | None: ...

    async def list_cell_attributions(self, notebook_id: str) -> list[CellAttribution]: ...

    async def evict_notebook(self, notebook_id: str) -> None: ...


@dataclass(frozen=True)
class MemoryLedgerPolicy:
    max_completed_jobs_per_notebook: int = 20


class MemoryLedger:
    """In-memory notebook-scoped ledger.

    Jobs remain queryable while a notebook runtime is alive and retain a small
    bounded recent history. Notebook eviction clears both jobs and attribution.
    """

    def __init__(self, policy: MemoryLedgerPolicy | None = None):
        self._policy = policy or MemoryLedgerPolicy()
        self._lock = asyncio.Lock()
        self._jobs_by_id: dict[str, Job] = {}
        self._job_ids_by_notebook: dict[str, list[str]] = defaultdict(list)
        self._cell_attribution: dict[tuple[str, str], CellAttribution] = {}

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        async with self._lock:
            self._jobs_by_id.clear()
            self._job_ids_by_notebook.clear()
            self._cell_attribution.clear()

    async def create_job(
        self,
        notebook_id: str,
        actor_id: str,
        actor_type: ActorType,
        action: JobAction,
        target_cells: str | None = None,
        runtime_id: str | None = None,
    ) -> Job:
        async with self._lock:
            job = Job(
                job_id=uuid.uuid4().hex[:12],
                notebook_id=notebook_id,
                actor_id=actor_id,
                actor_type=actor_type,
                action=action,
                status=JobStatus.QUEUED,
                created_at=time.time(),
                runtime_id=runtime_id,
                target_cells=target_cells,
                request_uids=[],
            )
            self._jobs_by_id[job.job_id] = job
            self._job_ids_by_notebook[notebook_id].append(job.job_id)
            self._prune_completed_jobs_locked(notebook_id)
            return _copy_job(job)

    async def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        runtime_id: str | None = None,
    ) -> None:
        async with self._lock:
            job = self._jobs_by_id.get(job_id)
            if job is None:
                return

            now = time.time()
            job.status = status
            if status == JobStatus.RUNNING and job.started_at is None:
                job.started_at = now
            if status in {
                JobStatus.SUCCEEDED,
                JobStatus.FAILED,
                JobStatus.INTERRUPTED,
            }:
                job.completed_at = now
            if runtime_id is not None:
                job.runtime_id = runtime_id

            self._prune_completed_jobs_locked(job.notebook_id)

    async def append_request_uid(self, job_id: str, request_uid: str) -> None:
        async with self._lock:
            job = self._jobs_by_id.get(job_id)
            if job is None:
                raise ValueError(f"Job {job_id} not found")
            job.request_uids.append(request_uid)

    async def get_job(self, job_id: str) -> Job | None:
        async with self._lock:
            job = self._jobs_by_id.get(job_id)
            return None if job is None else _copy_job(job)

    async def list_jobs(
        self,
        notebook_id: str | None = None,
        status: JobStatus | None = None,
        limit: int = 100,
    ) -> list[Job]:
        async with self._lock:
            jobs = self._iter_jobs_locked(notebook_id=notebook_id, status=status)
            return [_copy_job(job) for job in jobs[:limit]]

    async def list_active_jobs(self, notebook_id: str) -> list[Job]:
        async with self._lock:
            jobs = self._iter_jobs_locked(notebook_id=notebook_id)
            active = [
                job
                for job in jobs
                if job.status in {
                    JobStatus.QUEUED,
                    JobStatus.RUNNING,
                    JobStatus.AWAITING_INPUT,
                }
            ]
            return [_copy_job(job) for job in active]

    async def update_cell_attribution(
        self,
        notebook_id: str,
        cell_id: str,
        editor_id: str | None = None,
        editor_type: ActorType | None = None,
        executor_id: str | None = None,
        executor_type: ActorType | None = None,
    ) -> None:
        async with self._lock:
            key = (notebook_id, cell_id)
            existing = self._cell_attribution.get(key)
            now = time.time()
            if existing is None:
                existing = CellAttribution(
                    notebook_id=notebook_id,
                    cell_id=cell_id,
                    updated_at=now,
                )
                self._cell_attribution[key] = existing

            if editor_id is not None:
                existing.last_editor_id = editor_id
                existing.last_editor_type = editor_type.value if editor_type else None
            if executor_id is not None:
                existing.last_executor_id = executor_id
                existing.last_executor_type = executor_type.value if executor_type else None
            existing.updated_at = now

    async def get_cell_attribution(
        self,
        notebook_id: str,
        cell_id: str,
    ) -> CellAttribution | None:
        async with self._lock:
            attr = self._cell_attribution.get((notebook_id, cell_id))
            return None if attr is None else _copy_attribution(attr)

    async def list_cell_attributions(self, notebook_id: str) -> list[CellAttribution]:
        async with self._lock:
            attrs = [
                attr
                for attr in self._cell_attribution.values()
                if attr.notebook_id == notebook_id
            ]
            attrs.sort(key=lambda attr: attr.updated_at, reverse=True)
            return [_copy_attribution(attr) for attr in attrs]

    async def evict_notebook(self, notebook_id: str) -> None:
        async with self._lock:
            for job_id in self._job_ids_by_notebook.pop(notebook_id, []):
                self._jobs_by_id.pop(job_id, None)

            stale_keys = [
                key for key in self._cell_attribution if key[0] == notebook_id
            ]
            for key in stale_keys:
                self._cell_attribution.pop(key, None)

    def _iter_jobs_locked(
        self,
        *,
        notebook_id: str | None = None,
        status: JobStatus | None = None,
    ) -> list[Job]:
        if notebook_id is None:
            jobs = list(self._jobs_by_id.values())
        else:
            jobs = [
                self._jobs_by_id[job_id]
                for job_id in self._job_ids_by_notebook.get(notebook_id, [])
                if job_id in self._jobs_by_id
            ]

        if status is not None:
            jobs = [job for job in jobs if job.status == status]

        jobs.sort(key=lambda job: job.created_at, reverse=True)
        return jobs

    def _prune_completed_jobs_locked(self, notebook_id: str) -> None:
        max_completed = self._policy.max_completed_jobs_per_notebook
        if max_completed < 0:
            return

        completed_ids = [
            job_id
            for job_id in self._job_ids_by_notebook.get(notebook_id, [])
            if self._jobs_by_id.get(job_id) is not None
            and self._jobs_by_id[job_id].status
            in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.INTERRUPTED}
        ]
        overflow = len(completed_ids) - max_completed
        if overflow <= 0:
            return

        stale_ids = set(completed_ids[:overflow])
        self._job_ids_by_notebook[notebook_id] = [
            job_id
            for job_id in self._job_ids_by_notebook.get(notebook_id, [])
            if job_id not in stale_ids
        ]
        for job_id in stale_ids:
            self._jobs_by_id.pop(job_id, None)


def _copy_job(job: Job) -> Job:
    return Job(
        job_id=job.job_id,
        notebook_id=job.notebook_id,
        runtime_id=job.runtime_id,
        actor_id=job.actor_id,
        actor_type=job.actor_type,
        action=job.action,
        status=job.status,
        created_at=job.created_at,
        target_cells=job.target_cells,
        request_uids=list(job.request_uids),
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


def _copy_attribution(attr: CellAttribution) -> CellAttribution:
    return CellAttribution(
        notebook_id=attr.notebook_id,
        cell_id=attr.cell_id,
        last_editor_id=attr.last_editor_id,
        last_editor_type=attr.last_editor_type,
        last_executor_id=attr.last_executor_id,
        last_executor_type=attr.last_executor_type,
        updated_at=attr.updated_at,
    )
