"""Tests for Hypernote MCP server tool handlers."""

import asyncio
import json

import pytest

from hypernote.actor_ledger import ActorLedger, ActorType
from hypernote.execution_orchestrator import (
    ExecutionBackend,
    ExecutionOrchestrator,
    ExecutionResult,
    ExecutionStatus,
    NotebookBackend,
)
from hypernote.runtime_manager import KernelBackend, RuntimeManager, RuntimePolicy
from hypernote.mcp.server import HypernoteMCPServer


# --- Reuse mock backends ---

class _MockKernel(KernelBackend):
    def __init__(self):
        self._n = 0
        self._alive = set()

    async def start_kernel(self, kernel_name="python3"):
        self._n += 1
        kid = f"k-{self._n}"
        self._alive.add(kid)
        return kid, f"s-{self._n}"

    async def shutdown_kernel(self, kid):
        self._alive.discard(kid)

    async def interrupt_kernel(self, kid):
        pass

    async def is_kernel_alive(self, kid):
        return kid in self._alive


class _MockExec(ExecutionBackend):
    def __init__(self):
        self._results = {}
        self._n = 0

    async def execute(self, kernel_id, code):
        self._n += 1
        uid = f"req-{self._n}"
        self._results[uid] = ExecutionResult(status=ExecutionStatus.OK, outputs=[])
        return uid

    async def poll_result(self, kernel_id, uid):
        return self._results.get(uid, ExecutionResult(status=ExecutionStatus.PENDING))

    async def send_input(self, kernel_id, value):
        pass


class _MockNb(NotebookBackend):
    def __init__(self):
        self._nbs = {}
        self._n = 0

    async def create_notebook(self, path):
        self._n += 1
        nid = f"nb-{self._n}"
        self._nbs[nid] = []
        return nid

    async def open_notebook(self, path):
        return path

    async def save_notebook(self, nid):
        pass

    async def list_cells(self, nid):
        return self._nbs.get(nid, [])

    async def get_cell_source(self, nid, cid):
        for c in self._nbs.get(nid, []):
            if c["id"] == cid:
                return c["source"]
        raise ValueError

    async def insert_cell(self, nid, idx, ctype, source):
        cells = self._nbs.setdefault(nid, [])
        cid = f"cell-{len(cells)}"
        cells.insert(idx, {"id": cid, "type": ctype, "source": source})
        return cid

    async def replace_cell_source(self, nid, cid, source):
        for c in self._nbs.get(nid, []):
            if c["id"] == cid:
                c["source"] = source

    async def delete_cell(self, nid, cid):
        cells = self._nbs.get(nid, [])
        self._nbs[nid] = [c for c in cells if c["id"] != cid]


@pytest.fixture
async def mcp():
    ledger = ActorLedger(":memory:")
    await ledger.initialize()
    orch = ExecutionOrchestrator(
        ledger,
        RuntimeManager(_MockKernel(), RuntimePolicy()),
        _MockExec(),
        _MockNb(),
    )
    server = HypernoteMCPServer(orch, actor_id="test-agent")
    yield server
    await ledger.close()


async def _call(mcp: HypernoteMCPServer, name: str, args: dict) -> dict:
    """Call a tool handler directly and parse the JSON result."""
    handler = getattr(mcp, f"_handle_{name}")
    result = await handler(args)
    return result


# --- Tests ---

async def test_create_and_list_cells(mcp: HypernoteMCPServer):
    result = await _call(mcp, "notebook_create", {"path": "test.ipynb"})
    nb_id = result["notebook_id"]

    await _call(mcp, "notebook_insert_cell", {
        "notebook_id": nb_id, "index": 0, "source": "x = 1", "cell_type": "code",
    })
    await _call(mcp, "notebook_insert_cell", {
        "notebook_id": nb_id, "index": 1, "source": "print(x)", "cell_type": "code",
    })

    result = await _call(mcp, "notebook_list_cells", {"notebook_id": nb_id})
    assert len(result["cells"]) == 2
    assert result["cells"][0]["source"] == "x = 1"


async def test_read_cell(mcp: HypernoteMCPServer):
    result = await _call(mcp, "notebook_create", {"path": "t.ipynb"})
    nb_id = result["notebook_id"]
    result = await _call(mcp, "notebook_insert_cell", {
        "notebook_id": nb_id, "index": 0, "source": "hello", "cell_type": "code",
    })
    cell_id = result["cell_id"]

    result = await _call(mcp, "notebook_read_cell", {"notebook_id": nb_id, "cell_id": cell_id})
    assert result["source"] == "hello"


async def test_replace_and_delete_cell(mcp: HypernoteMCPServer):
    result = await _call(mcp, "notebook_create", {"path": "t.ipynb"})
    nb_id = result["notebook_id"]
    result = await _call(mcp, "notebook_insert_cell", {
        "notebook_id": nb_id, "index": 0, "source": "old", "cell_type": "code",
    })
    cell_id = result["cell_id"]

    await _call(mcp, "notebook_replace_cell", {
        "notebook_id": nb_id, "cell_id": cell_id, "source": "new",
    })
    result = await _call(mcp, "notebook_read_cell", {"notebook_id": nb_id, "cell_id": cell_id})
    assert result["source"] == "new"

    await _call(mcp, "notebook_delete_cell", {"notebook_id": nb_id, "cell_id": cell_id})
    result = await _call(mcp, "notebook_list_cells", {"notebook_id": nb_id})
    assert len(result["cells"]) == 0


async def test_execute_and_job_lifecycle(mcp: HypernoteMCPServer):
    result = await _call(mcp, "notebook_create", {"path": "t.ipynb"})
    nb_id = result["notebook_id"]
    result = await _call(mcp, "notebook_insert_cell", {
        "notebook_id": nb_id, "index": 0, "source": "1+1", "cell_type": "code",
    })
    cell_id = result["cell_id"]

    result = await _call(mcp, "notebook_execute", {"notebook_id": nb_id, "cell_ids": [cell_id]})
    job_id = result["job_id"]
    assert result["status"] == "queued"

    await asyncio.sleep(0.3)

    result = await _call(mcp, "job_get", {"job_id": job_id})
    assert result["status"] == "succeeded"


async def test_job_list(mcp: HypernoteMCPServer):
    result = await _call(mcp, "notebook_create", {"path": "t.ipynb"})
    nb_id = result["notebook_id"]
    result = await _call(mcp, "notebook_insert_cell", {
        "notebook_id": nb_id, "index": 0, "source": "x", "cell_type": "code",
    })
    cell_id = result["cell_id"]

    await _call(mcp, "notebook_execute", {"notebook_id": nb_id, "cell_ids": [cell_id]})
    await asyncio.sleep(0.3)

    result = await _call(mcp, "job_list", {"notebook_id": nb_id})
    assert len(result["jobs"]) >= 1


async def test_runtime_lifecycle(mcp: HypernoteMCPServer):
    result = await _call(mcp, "notebook_create", {"path": "t.ipynb"})
    nb_id = result["notebook_id"]

    result = await _call(mcp, "runtime_open", {"notebook_id": nb_id})
    assert result["state"] == "live-attached"

    result = await _call(mcp, "runtime_status", {"notebook_id": nb_id})
    assert result["state"] == "live-attached"

    result = await _call(mcp, "runtime_stop", {"notebook_id": nb_id})
    assert result["state"] == "stopped"


async def test_notebook_status(mcp: HypernoteMCPServer):
    result = await _call(mcp, "notebook_create", {"path": "t.ipynb"})
    nb_id = result["notebook_id"]
    result = await _call(mcp, "notebook_status", {"notebook_id": nb_id})
    assert "runtime" in result
    assert "active_jobs" in result


async def test_save(mcp: HypernoteMCPServer):
    result = await _call(mcp, "notebook_create", {"path": "t.ipynb"})
    result = await _call(mcp, "notebook_save", {"notebook_id": result["notebook_id"]})
    assert result["saved"]


async def test_job_await(mcp: HypernoteMCPServer):
    result = await _call(mcp, "notebook_create", {"path": "t.ipynb"})
    nb_id = result["notebook_id"]
    result = await _call(mcp, "notebook_insert_cell", {
        "notebook_id": nb_id, "index": 0, "source": "1", "cell_type": "code",
    })
    cell_id = result["cell_id"]

    result = await _call(mcp, "notebook_execute", {"notebook_id": nb_id, "cell_ids": [cell_id]})
    job_id = result["job_id"]

    result = await _call(mcp, "job_await", {"job_id": job_id, "timeout_seconds": 5})
    assert result["status"] == "succeeded"
