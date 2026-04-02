"""ActorLedger: SQLite persistence for job and cell attribution metadata.

Stores only metadata about who did what and when.
Never stores notebook content or output copies.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id        TEXT PRIMARY KEY,
    notebook_id   TEXT NOT NULL,
    runtime_id    TEXT,
    actor_id      TEXT NOT NULL,
    actor_type    TEXT NOT NULL,
    action        TEXT NOT NULL,
    target_cells  TEXT,
    request_uids  TEXT NOT NULL DEFAULT '[]',
    status        TEXT NOT NULL,
    created_at    REAL NOT NULL,
    started_at    REAL,
    completed_at  REAL,
    reconnect_ref TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_notebook ON jobs(notebook_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS cell_attribution (
    notebook_id        TEXT NOT NULL,
    cell_id            TEXT NOT NULL,
    last_editor_id     TEXT,
    last_editor_type   TEXT,
    last_executor_id   TEXT,
    last_executor_type TEXT,
    updated_at         REAL NOT NULL,
    PRIMARY KEY (notebook_id, cell_id)
);
"""


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
    target_cells: str | None = None  # JSON array of cell_ids
    request_uids: list[str] = field(default_factory=list)
    started_at: float | None = None
    completed_at: float | None = None
    reconnect_ref: str | None = None


@dataclass
class CellAttribution:
    notebook_id: str
    cell_id: str
    updated_at: float
    last_editor_id: str | None = None
    last_editor_type: str | None = None
    last_executor_id: str | None = None
    last_executor_type: str | None = None


class ActorLedger:
    """Tiny persistent store for job tracking and cell attribution.

    SQLite-backed. No notebook content — only who did what and when.
    """

    def __init__(self, db_path: str | Path = ":memory:"):
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._ensure_column("jobs", "request_uids", "TEXT NOT NULL DEFAULT '[]'")
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("ActorLedger not initialized — call initialize() first")
        return self._db

    async def _ensure_column(self, table: str, column: str, definition: str) -> None:
        cursor = await self.db.execute(f"PRAGMA table_info({table})")
        rows = await cursor.fetchall()
        existing = {row["name"] for row in rows}
        if column not in existing:
            await self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    # --- Jobs ---

    async def create_job(
        self,
        notebook_id: str,
        actor_id: str,
        actor_type: ActorType,
        action: JobAction,
        target_cells: str | None = None,
        runtime_id: str | None = None,
    ) -> Job:
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
        await self.db.execute(
            """INSERT INTO jobs
               (job_id, notebook_id, runtime_id, actor_id, actor_type,
                action, target_cells, request_uids, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job.job_id,
                job.notebook_id,
                job.runtime_id,
                job.actor_id,
                job.actor_type.value,
                job.action.value,
                job.target_cells,
                json.dumps(job.request_uids),
                job.status.value,
                job.created_at,
            ),
        )
        await self.db.commit()
        return job

    async def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        runtime_id: str | None = None,
        reconnect_ref: str | None = None,
    ) -> None:
        now = time.time()
        started = now if status == JobStatus.RUNNING else None
        completed = (
            now
            if status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.INTERRUPTED)
            else None
        )

        sets = ["status = ?"]
        params: list = [status.value]

        if started:
            sets.append("started_at = ?")
            params.append(started)
        if completed:
            sets.append("completed_at = ?")
            params.append(completed)
        if runtime_id is not None:
            sets.append("runtime_id = ?")
            params.append(runtime_id)
        if reconnect_ref is not None:
            sets.append("reconnect_ref = ?")
            params.append(reconnect_ref)

        params.append(job_id)
        await self.db.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE job_id = ?", params)
        await self.db.commit()

    async def append_request_uid(self, job_id: str, request_uid: str) -> None:
        job = await self.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        request_uids = list(job.request_uids)
        request_uids.append(request_uid)
        await self.db.execute(
            "UPDATE jobs SET request_uids = ? WHERE job_id = ?",
            (json.dumps(request_uids), job_id),
        )
        await self.db.commit()

    async def get_job(self, job_id: str) -> Job | None:
        cursor = await self.db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_job(row)

    async def list_jobs(
        self,
        notebook_id: str | None = None,
        status: JobStatus | None = None,
        limit: int = 100,
    ) -> list[Job]:
        where_clauses = []
        params: list = []
        if notebook_id:
            where_clauses.append("notebook_id = ?")
            params.append(notebook_id)
        if status:
            where_clauses.append("status = ?")
            params.append(status.value)

        where = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        params.append(limit)
        cursor = await self.db.execute(
            f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT ?", params
        )
        rows = await cursor.fetchall()
        return [_row_to_job(r) for r in rows]

    async def list_active_jobs(self, notebook_id: str) -> list[Job]:
        cursor = await self.db.execute(
            (
                "SELECT * FROM jobs "
                "WHERE notebook_id = ? AND status IN (?, ?, ?) "
                "ORDER BY created_at"
            ),
            (
                notebook_id,
                JobStatus.QUEUED.value,
                JobStatus.RUNNING.value,
                JobStatus.AWAITING_INPUT.value,
            ),
        )
        rows = await cursor.fetchall()
        return [_row_to_job(r) for r in rows]

    # --- Cell attribution ---

    async def update_cell_attribution(
        self,
        notebook_id: str,
        cell_id: str,
        editor_id: str | None = None,
        editor_type: ActorType | None = None,
        executor_id: str | None = None,
        executor_type: ActorType | None = None,
    ) -> None:
        now = time.time()
        await self.db.execute(
            """INSERT INTO cell_attribution
               (notebook_id, cell_id, last_editor_id, last_editor_type,
                last_executor_id, last_executor_type, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(notebook_id, cell_id) DO UPDATE SET
                 last_editor_id = COALESCE(
                   excluded.last_editor_id,
                   cell_attribution.last_editor_id
                 ),
                 last_editor_type = COALESCE(
                   excluded.last_editor_type,
                   cell_attribution.last_editor_type
                 ),
                 last_executor_id = COALESCE(
                   excluded.last_executor_id,
                   cell_attribution.last_executor_id
                 ),
                 last_executor_type = COALESCE(
                   excluded.last_executor_type,
                   cell_attribution.last_executor_type
                 ),
                 updated_at = excluded.updated_at""",
            (
                notebook_id,
                cell_id,
                editor_id,
                editor_type.value if editor_type else None,
                executor_id,
                executor_type.value if executor_type else None,
                now,
            ),
        )
        await self.db.commit()

    async def get_cell_attribution(
        self, notebook_id: str, cell_id: str
    ) -> CellAttribution | None:
        cursor = await self.db.execute(
            "SELECT * FROM cell_attribution WHERE notebook_id = ? AND cell_id = ?",
            (notebook_id, cell_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_attribution(row)

    async def list_cell_attributions(self, notebook_id: str) -> list[CellAttribution]:
        cursor = await self.db.execute(
            "SELECT * FROM cell_attribution WHERE notebook_id = ? ORDER BY updated_at DESC",
            (notebook_id,),
        )
        rows = await cursor.fetchall()
        return [_row_to_attribution(r) for r in rows]


def _row_to_job(row: aiosqlite.Row) -> Job:
    return Job(
        job_id=row["job_id"],
        notebook_id=row["notebook_id"],
        runtime_id=row["runtime_id"],
        actor_id=row["actor_id"],
        actor_type=ActorType(row["actor_type"]),
        action=JobAction(row["action"]),
        status=JobStatus(row["status"]),
        created_at=row["created_at"],
        target_cells=row["target_cells"],
        request_uids=json.loads(row["request_uids"] or "[]"),
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        reconnect_ref=row["reconnect_ref"],
    )


def _row_to_attribution(row: aiosqlite.Row) -> CellAttribution:
    return CellAttribution(
        notebook_id=row["notebook_id"],
        cell_id=row["cell_id"],
        last_editor_id=row["last_editor_id"],
        last_editor_type=row["last_editor_type"],
        last_executor_id=row["last_executor_id"],
        last_executor_type=row["last_executor_type"],
        updated_at=row["updated_at"],
    )
