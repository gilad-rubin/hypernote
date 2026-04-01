"""Tests for ExecutionOrchestrator — attributed execution."""

import asyncio
import json
import uuid

import pytest

from hypernote.actor_ledger import ActorLedger, ActorType, JobAction, JobStatus
from hypernote.execution_orchestrator import (
    ExecutionBackend,
    ExecutionOrchestrator,
    ExecutionResult,
    ExecutionStatus,
    NotebookBackend,
)
from hypernote.runtime_manager import (
    KernelBackend,
    RuntimeManager,
    RuntimePolicy,
    RuntimeState,
)


# --- Mock backends ---

class MockKernelBackend(KernelBackend):
    def __init__(self):
        self._next = 0
        self._alive: set[str] = set()

    async def start_kernel(self, kernel_name="python3"):
        self._next += 1
        kid = f"k-{self._next}"
        self._alive.add(kid)
        return kid, f"s-{self._next}"

    async def shutdown_kernel(self, kernel_id):
        self._alive.discard(kernel_id)

    async def interrupt_kernel(self, kernel_id):
        pass

    async def is_kernel_alive(self, kernel_id):
        return kernel_id in self._alive


class MockExecutionBackend(ExecutionBackend):
    def __init__(self):
        self.results: dict[str, ExecutionResult] = {}
        self.executed: list[tuple[str, str]] = []
        self.stdin_sent: list[tuple[str, str]] = []

    async def execute(self, kernel_id, code):
        uid = uuid.uuid4().hex[:8]
        self.executed.append((kernel_id, code))
        # Default: succeed immediately
        self.results[uid] = ExecutionResult(
            status=ExecutionStatus.OK, execution_count=len(self.executed), outputs=[]
        )
        return uid

    async def poll_result(self, kernel_id, request_uid):
        return self.results.get(
            request_uid,
            ExecutionResult(status=ExecutionStatus.PENDING),
        )

    async def send_input(self, kernel_id, value):
        self.stdin_sent.append((kernel_id, value))


class MockNotebookBackend(NotebookBackend):
    def __init__(self):
        self._notebooks: dict[str, list[dict]] = {}
        self._next_nb = 0

    async def get_cell_source(self, notebook_id, cell_id):
        for cell in self._notebooks.get(notebook_id, []):
            if cell["id"] == cell_id:
                return cell["source"]
        raise ValueError(f"Cell {cell_id} not found")

    async def list_cells(self, notebook_id):
        return self._notebooks.get(notebook_id, [])

    async def insert_cell(self, notebook_id, index, cell_type, source):
        cells = self._notebooks.setdefault(notebook_id, [])
        cell_id = f"cell-{len(cells)}"
        cell = {"id": cell_id, "type": cell_type, "source": source}
        cells.insert(index, cell)
        return cell_id

    async def replace_cell_source(self, notebook_id, cell_id, source):
        for cell in self._notebooks.get(notebook_id, []):
            if cell["id"] == cell_id:
                cell["source"] = source
                return
        raise ValueError(f"Cell {cell_id} not found")

    async def delete_cell(self, notebook_id, cell_id):
        cells = self._notebooks.get(notebook_id, [])
        self._notebooks[notebook_id] = [c for c in cells if c["id"] != cell_id]

    async def create_notebook(self, path):
        self._next_nb += 1
        nb_id = f"nb-{self._next_nb}"
        self._notebooks[nb_id] = []
        return nb_id

    async def open_notebook(self, path):
        return path

    async def save_notebook(self, notebook_id):
        pass


# --- Fixtures ---

@pytest.fixture
async def components():
    ledger = ActorLedger(":memory:")
    await ledger.initialize()
    kernel = MockKernelBackend()
    runtime_mgr = RuntimeManager(kernel, RuntimePolicy())
    exec_backend = MockExecutionBackend()
    nb_backend = MockNotebookBackend()
    orch = ExecutionOrchestrator(ledger, runtime_mgr, exec_backend, nb_backend)
    yield orch, ledger, exec_backend, nb_backend
    await ledger.close()


# --- Tests ---

async def test_queue_execution_creates_job(components):
    orch, ledger, exec_be, nb_be = components
    nb_id = await orch.create_notebook("test.ipynb")
    cell_id = await orch.insert_cell(nb_id, 0, "code", "print('hi')", "agent-1", ActorType.AGENT)

    job = await orch.queue_execution(nb_id, [cell_id], "agent-1", ActorType.AGENT)
    assert job.status == JobStatus.QUEUED
    assert job.actor_id == "agent-1"

    # Let the background task run
    await asyncio.sleep(0.2)

    updated = await orch.get_job(job.job_id)
    assert updated.status == JobStatus.SUCCEEDED


async def test_execution_records_attribution(components):
    orch, ledger, exec_be, nb_be = components
    nb_id = await orch.create_notebook("test.ipynb")
    cell_id = await orch.insert_cell(nb_id, 0, "code", "x = 1", "agent-1", ActorType.AGENT)

    job = await orch.queue_execution(nb_id, [cell_id], "user-gilad", ActorType.HUMAN)
    await asyncio.sleep(0.2)

    attr = await ledger.get_cell_attribution(nb_id, cell_id)
    assert attr.last_editor_id == "agent-1"
    assert attr.last_executor_id == "user-gilad"


async def test_execution_creates_runtime(components):
    orch, ledger, exec_be, nb_be = components
    nb_id = await orch.create_notebook("test.ipynb")
    cell_id = await orch.insert_cell(nb_id, 0, "code", "1+1", "a", ActorType.AGENT)

    await orch.queue_execution(nb_id, [cell_id], "a", ActorType.AGENT)
    status = await orch.get_runtime_status(nb_id)
    assert status["state"] in ("live-attached", "live-detached")
    assert status["kernel_id"] is not None


async def test_interrupt(components):
    orch, ledger, exec_be, nb_be = components
    nb_id = await orch.create_notebook("test.ipynb")
    cell_id = await orch.insert_cell(nb_id, 0, "code", "x=1", "a", ActorType.AGENT)

    await orch.queue_execution(nb_id, [cell_id], "a", ActorType.AGENT)
    await asyncio.sleep(0.1)

    await orch.interrupt(nb_id, "user-gilad", ActorType.HUMAN)
    jobs = await orch.list_jobs(notebook_id=nb_id)
    interrupt_jobs = [j for j in jobs if j.action == JobAction.INTERRUPT]
    assert len(interrupt_jobs) == 1
    assert interrupt_jobs[0].actor_id == "user-gilad"


async def test_list_active_jobs(components):
    orch, ledger, exec_be, nb_be = components
    nb_id = await orch.create_notebook("test.ipynb")
    c1 = await orch.insert_cell(nb_id, 0, "code", "a=1", "a", ActorType.AGENT)
    c2 = await orch.insert_cell(nb_id, 1, "code", "b=2", "a", ActorType.AGENT)

    await orch.queue_execution(nb_id, [c1], "a", ActorType.AGENT)
    await asyncio.sleep(0.2)  # Let first job complete

    j2 = await orch.queue_execution(nb_id, [c2], "a", ActorType.AGENT)
    active = await orch.list_active_jobs(nb_id)
    # j2 might be queued or running
    assert any(j.job_id == j2.job_id for j in active)


async def test_notebook_operations_with_attribution(components):
    orch, ledger, exec_be, nb_be = components
    nb_id = await orch.create_notebook("test.ipynb")

    cell_id = await orch.insert_cell(nb_id, 0, "code", "x = 1", "agent-1", ActorType.AGENT)
    attr = await ledger.get_cell_attribution(nb_id, cell_id)
    assert attr.last_editor_id == "agent-1"

    await orch.replace_cell_source(nb_id, cell_id, "x = 2", "user-gilad", ActorType.HUMAN)
    attr = await ledger.get_cell_attribution(nb_id, cell_id)
    assert attr.last_editor_id == "user-gilad"

    cells = await orch.list_cells(nb_id)
    assert len(cells) == 1
    assert cells[0]["source"] == "x = 2"


async def test_get_runtime_status_no_runtime(components):
    orch, *_ = components
    status = await orch.get_runtime_status("nonexistent")
    assert status["state"] == "stopped"


async def test_execution_with_error(components):
    orch, ledger, exec_be, nb_be = components
    nb_id = await orch.create_notebook("test.ipynb")
    cell_id = await orch.insert_cell(nb_id, 0, "code", "raise Exception()", "a", ActorType.AGENT)

    # Make the execution return an error
    original_execute = exec_be.execute

    async def error_execute(kernel_id, code):
        uid = await original_execute(kernel_id, code)
        exec_be.results[uid] = ExecutionResult(
            status=ExecutionStatus.ERROR, error="Exception"
        )
        return uid

    exec_be.execute = error_execute

    job = await orch.queue_execution(nb_id, [cell_id], "a", ActorType.AGENT)
    await asyncio.sleep(0.2)

    updated = await orch.get_job(job.job_id)
    assert updated.status == JobStatus.FAILED
