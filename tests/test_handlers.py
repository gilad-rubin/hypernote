"""Tests for Hypernote REST handlers.

Spins up a tornado app with mock backends — no Jupyter Server required.
"""

import json

import httpx
import pytest
import tornado.web
import tornado.httpserver

from hypernote.actor_ledger import ActorLedger, ActorType
from hypernote.execution_orchestrator import (
    ExecutionBackend,
    ExecutionOrchestrator,
    ExecutionResult,
    ExecutionStatus,
    NotebookBackend,
)
from hypernote.runtime_manager import KernelBackend, RuntimeManager, RuntimePolicy
from hypernote.server.handlers import (
    CellAttributionHandler,
    CellHandler,
    CellsHandler,
    ExecuteHandler,
    InterruptHandler,
    JobHandler,
    JobsHandler,
    NotebookHandler,
    NotebooksHandler,
    RuntimeOpenHandler,
    RuntimeStatusHandler,
    RuntimeStopHandler,
    SaveHandler,
    SendStdinHandler,
)


# --- Mock backends (same as orchestrator tests) ---

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
        self._nbs: dict[str, list[dict]] = {}
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
                return

    async def delete_cell(self, nid, cid):
        cells = self._nbs.get(nid, [])
        self._nbs[nid] = [c for c in cells if c["id"] != cid]


# --- Fixtures ---

@pytest.fixture
async def app():
    """Create a test tornado app with mock backends."""
    ledger = ActorLedger(":memory:")
    await ledger.initialize()

    orch = ExecutionOrchestrator(
        ledger,
        RuntimeManager(_MockKernel(), RuntimePolicy()),
        _MockExec(),
        _MockNb(),
    )

    async def get_orch():
        return orch

    kwargs = {"get_orchestrator": get_orch}

    application = tornado.web.Application([
        (r"/notebooks", NotebooksHandler, kwargs),
        (r"/notebooks/([^/]+)", NotebookHandler, kwargs),
        (r"/notebooks/([^/]+)/save", SaveHandler, kwargs),
        (r"/notebooks/([^/]+)/cells", CellsHandler, kwargs),
        (r"/notebooks/([^/]+)/cells/([^/]+)", CellHandler, kwargs),
        (r"/notebooks/([^/]+)/cells/([^/]+)/attribution", CellAttributionHandler, kwargs),
        (r"/notebooks/([^/]+)/execute", ExecuteHandler, kwargs),
        (r"/jobs", JobsHandler, kwargs),
        (r"/jobs/([^/]+)", JobHandler, kwargs),
        (r"/jobs/([^/]+)/stdin", SendStdinHandler, kwargs),
        (r"/notebooks/([^/]+)/runtime", RuntimeStatusHandler, kwargs),
        (r"/notebooks/([^/]+)/runtime/open", RuntimeOpenHandler, kwargs),
        (r"/notebooks/([^/]+)/runtime/stop", RuntimeStopHandler, kwargs),
        (r"/notebooks/([^/]+)/interrupt", InterruptHandler, kwargs),
    ])

    server = tornado.httpserver.HTTPServer(application)
    server.listen(0)  # random port
    port = list(server._sockets.values())[0].getsockname()[1]

    yield f"http://127.0.0.1:{port}"

    server.stop()
    await ledger.close()


@pytest.fixture
def client(app):
    return httpx.AsyncClient(base_url=app)


# --- Tests ---

async def test_create_notebook(client):
    async with client as c:
        resp = await c.post("/notebooks", json={"path": "test.ipynb"})
        assert resp.status_code == 201
        assert "notebook_id" in resp.json()


async def test_notebook_cell_lifecycle(client):
    async with client as c:
        # Create notebook
        resp = await c.post("/notebooks", json={"path": "test.ipynb"})
        nb_id = resp.json()["notebook_id"]

        # Insert cell
        resp = await c.post(
            f"/notebooks/{nb_id}/cells",
            json={"source": "x = 42", "cell_type": "code", "index": 0},
            headers={"X-Hypernote-Actor-Id": "agent-1", "X-Hypernote-Actor-Type": "agent"},
        )
        assert resp.status_code == 201
        cell_id = resp.json()["cell_id"]

        # List cells
        resp = await c.get(f"/notebooks/{nb_id}/cells")
        cells = resp.json()["cells"]
        assert len(cells) == 1
        assert cells[0]["source"] == "x = 42"

        # Replace cell source
        resp = await c.put(
            f"/notebooks/{nb_id}/cells/{cell_id}",
            json={"source": "x = 100"},
            headers={"X-Hypernote-Actor-Id": "user-gilad", "X-Hypernote-Actor-Type": "human"},
        )
        assert resp.status_code == 200

        # Check attribution
        resp = await c.get(f"/notebooks/{nb_id}/cells/{cell_id}/attribution")
        attr = resp.json()
        assert attr["last_editor_id"] == "user-gilad"

        # Delete cell
        resp = await c.delete(f"/notebooks/{nb_id}/cells/{cell_id}")
        assert resp.status_code == 200

        resp = await c.get(f"/notebooks/{nb_id}/cells")
        assert len(resp.json()["cells"]) == 0


async def test_execute_and_get_job(client):
    async with client as c:
        resp = await c.post("/notebooks", json={"path": "t.ipynb"})
        nb_id = resp.json()["notebook_id"]

        resp = await c.post(
            f"/notebooks/{nb_id}/cells",
            json={"source": "print('hi')", "index": 0},
            headers={"X-Hypernote-Actor-Id": "agent-1", "X-Hypernote-Actor-Type": "agent"},
        )
        cell_id = resp.json()["cell_id"]

        # Execute
        resp = await c.post(
            f"/notebooks/{nb_id}/execute",
            json={"cell_ids": [cell_id]},
            headers={"X-Hypernote-Actor-Id": "agent-1", "X-Hypernote-Actor-Type": "agent"},
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        # Get job
        resp = await c.get(f"/jobs/{job_id}")
        assert resp.status_code == 200
        assert resp.json()["actor_id"] == "agent-1"

        # List jobs
        resp = await c.get(f"/jobs?notebook_id={nb_id}")
        assert len(resp.json()["jobs"]) >= 1


async def test_runtime_lifecycle(client):
    async with client as c:
        resp = await c.post("/notebooks", json={"path": "t.ipynb"})
        nb_id = resp.json()["notebook_id"]

        # Open runtime
        resp = await c.post(f"/notebooks/{nb_id}/runtime/open", json={"client_id": "cli-1"})
        assert resp.status_code == 200
        assert resp.json()["state"] == "live-attached"

        # Get status
        resp = await c.get(f"/notebooks/{nb_id}/runtime")
        assert resp.json()["state"] == "live-attached"

        # Stop
        resp = await c.post(f"/notebooks/{nb_id}/runtime/stop", json={})
        assert resp.status_code == 200
        assert resp.json()["state"] == "stopped"


async def test_save_notebook(client):
    async with client as c:
        resp = await c.post("/notebooks", json={"path": "t.ipynb"})
        nb_id = resp.json()["notebook_id"]
        resp = await c.post(f"/notebooks/{nb_id}/save", json={})
        assert resp.status_code == 200


async def test_job_not_found(client):
    async with client as c:
        resp = await c.get("/jobs/nonexistent")
        assert resp.status_code == 404


async def test_execute_without_cell_ids_returns_400(client):
    async with client as c:
        resp = await c.post("/notebooks", json={"path": "t.ipynb"})
        nb_id = resp.json()["notebook_id"]
        resp = await c.post(f"/notebooks/{nb_id}/execute", json={})
        assert resp.status_code == 400
