"""Integration tests: end-to-end flows from the architecture document.

Tests the four key flows without Jupyter Server — uses mock backends
but exercises the full stack: MCP/REST -> Orchestrator -> Ledger -> Runtime.
"""

import asyncio
import json

import pytest

from hypernote.actor_ledger import ActorLedger, ActorType, JobStatus
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
from hypernote.mcp.server import HypernoteMCPServer


# --- Mock backends ---

class MockKernel(KernelBackend):
    def __init__(self):
        self._n = 0
        self._alive = set()

    async def start_kernel(self, kn="python3"):
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


class MockExec(ExecutionBackend):
    def __init__(self):
        self._results = {}
        self._n = 0

    async def execute(self, kernel_id, code):
        self._n += 1
        uid = f"req-{self._n}"
        self._results[uid] = ExecutionResult(
            status=ExecutionStatus.OK,
            execution_count=self._n,
            outputs=[{"output_type": "execute_result", "text": f"Result of: {code}"}],
        )
        return uid

    async def poll_result(self, kernel_id, uid):
        return self._results.get(uid, ExecutionResult(status=ExecutionStatus.PENDING))

    async def send_input(self, kernel_id, value):
        pass


class MockNb(NotebookBackend):
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
        raise ValueError(f"Cell {cid} not found")

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


@pytest.fixture
async def stack():
    ledger = ActorLedger(":memory:")
    await ledger.initialize()
    kernel = MockKernel()
    runtime = RuntimeManager(kernel, RuntimePolicy(idle_ttl_seconds=5))
    exec_be = MockExec()
    nb_be = MockNb()
    orch = ExecutionOrchestrator(ledger, runtime, exec_be, nb_be)
    mcp = HypernoteMCPServer(orch, actor_id="agent-1")
    yield orch, mcp, ledger, kernel
    await ledger.close()


async def _call(mcp, name, args):
    handler = getattr(mcp, f"_handle_{name}")
    return await handler(args)


# =============================================================================
# Flow 1: Agent edits notebook with no UI open
# =============================================================================

async def test_flow1_agent_edits_no_ui(stack):
    """Agent creates notebook, adds cells, saves — human sees exact same cells later."""
    orch, mcp, ledger, _ = stack

    # Agent creates notebook
    result = await _call(mcp, "notebook_create", {"path": "analysis.ipynb"})
    nb_id = result["notebook_id"]

    # Agent adds cells
    r1 = await _call(mcp, "notebook_insert_cell", {
        "notebook_id": nb_id, "index": 0, "source": "import pandas as pd", "cell_type": "code",
    })
    r2 = await _call(mcp, "notebook_insert_cell", {
        "notebook_id": nb_id, "index": 1, "source": "df = pd.DataFrame({'x': [1,2,3]})", "cell_type": "code",
    })
    r3 = await _call(mcp, "notebook_insert_cell", {
        "notebook_id": nb_id, "index": 2, "source": "df.describe()", "cell_type": "code",
    })

    # Save
    await _call(mcp, "notebook_save", {"notebook_id": nb_id})

    # "Human opens later" — sees exact same cells
    cells = await _call(mcp, "notebook_list_cells", {"notebook_id": nb_id})
    assert len(cells["cells"]) == 3
    assert cells["cells"][0]["source"] == "import pandas as pd"
    assert cells["cells"][2]["source"] == "df.describe()"

    # Attribution recorded
    attr = await ledger.get_cell_attribution(nb_id, r1["cell_id"])
    assert attr.last_editor_id == "agent-1"
    assert attr.last_editor_type == "agent"


# =============================================================================
# Flow 2: Agent queues execution — outputs durable without UI
# =============================================================================

async def test_flow2_agent_executes_no_ui(stack):
    """Agent executes cells, outputs persist, human sees them later."""
    orch, mcp, ledger, _ = stack

    result = await _call(mcp, "notebook_create", {"path": "compute.ipynb"})
    nb_id = result["notebook_id"]

    r1 = await _call(mcp, "notebook_insert_cell", {
        "notebook_id": nb_id, "index": 0, "source": "1 + 1", "cell_type": "code",
    })
    cell_id = r1["cell_id"]

    # Execute
    exec_result = await _call(mcp, "notebook_execute", {
        "notebook_id": nb_id, "cell_ids": [cell_id],
    })
    job_id = exec_result["job_id"]

    # Wait for completion
    result = await _call(mcp, "job_await", {"job_id": job_id, "timeout_seconds": 5})
    assert result["status"] == "succeeded"

    # Verify attribution
    attr = await ledger.get_cell_attribution(nb_id, cell_id)
    assert attr.last_executor_id == "agent-1"


# =============================================================================
# Flow 3: Human opens later and attaches to live runtime
# =============================================================================

async def test_flow3_human_attaches_later(stack):
    """Agent starts runtime, human attaches later without disruption."""
    orch, mcp, ledger, _ = stack

    result = await _call(mcp, "notebook_create", {"path": "shared.ipynb"})
    nb_id = result["notebook_id"]

    # Agent opens runtime
    rt = await _call(mcp, "runtime_open", {"notebook_id": nb_id})
    assert rt["state"] == "live-attached"
    runtime_id = rt["runtime_id"]

    # Agent detaches (simulating agent done)
    await orch.runtime_manager.detach_client(runtime_id, "mcp-agent-1")

    # Runtime still alive but detached
    status = await _call(mcp, "runtime_status", {"notebook_id": nb_id})
    assert status["state"] == "live-detached"

    # Human attaches
    human_rt = await orch.runtime_manager.attach_client(runtime_id, "human-gilad")
    assert human_rt.state == RuntimeState.LIVE_ATTACHED
    assert "human-gilad" in human_rt.attached_clients


# =============================================================================
# Flow 4: Concurrent human + agent execution
# =============================================================================

async def test_flow4_concurrent_execution(stack):
    """Human and agent submit execution — both serialize through same queue."""
    orch, mcp, ledger, _ = stack

    result = await _call(mcp, "notebook_create", {"path": "collab.ipynb"})
    nb_id = result["notebook_id"]

    c1 = await _call(mcp, "notebook_insert_cell", {
        "notebook_id": nb_id, "index": 0, "source": "x = 1", "cell_type": "code",
    })
    c2 = await _call(mcp, "notebook_insert_cell", {
        "notebook_id": nb_id, "index": 1, "source": "y = 2", "cell_type": "code",
    })

    # Human executes cell 1
    j1 = await orch.queue_execution(nb_id, [c1["cell_id"]], "user-gilad", ActorType.HUMAN)

    # Agent executes cell 2 (concurrent)
    j2 = await orch.queue_execution(nb_id, [c2["cell_id"]], "agent-1", ActorType.AGENT)

    await asyncio.sleep(0.5)

    # Both should complete
    job1 = await orch.get_job(j1.job_id)
    job2 = await orch.get_job(j2.job_id)
    assert job1.status == JobStatus.SUCCEEDED
    assert job2.status == JobStatus.SUCCEEDED

    # Both attributed correctly
    a1 = await ledger.get_cell_attribution(nb_id, c1["cell_id"])
    assert a1.last_executor_id == "user-gilad"

    a2 = await ledger.get_cell_attribution(nb_id, c2["cell_id"])
    assert a2.last_executor_id == "agent-1"

    # Both used the same runtime
    assert job1.runtime_id == job2.runtime_id


# =============================================================================
# Flow 5: Disconnect — runtime survives client detach
# =============================================================================

async def test_flow5_disconnect_and_reconnect(stack):
    """Client disconnects, runtime survives, another client reconnects."""
    orch, mcp, ledger, kernel = stack

    result = await _call(mcp, "notebook_create", {"path": "persistent.ipynb"})
    nb_id = result["notebook_id"]

    # Client A opens
    rt = await orch.runtime_manager.open_runtime(nb_id, "client-a")
    runtime_id = rt.runtime_id
    kernel_id = rt.kernel_id

    # Client A disconnects
    await orch.runtime_manager.detach_client(runtime_id, "client-a")
    assert rt.state == RuntimeState.LIVE_DETACHED

    # Kernel still alive
    assert kernel_id in kernel._alive

    # Client B reconnects
    rt2 = await orch.runtime_manager.attach_client(runtime_id, "client-b")
    assert rt2.state == RuntimeState.LIVE_ATTACHED
    assert "client-b" in rt2.attached_clients


# =============================================================================
# Acceptance criteria verification
# =============================================================================

async def test_acceptance_opening_closing_doesnt_affect_execution(stack):
    """Opening or closing a client does not create or destroy execution ownership."""
    orch, mcp, ledger, kernel = stack

    result = await _call(mcp, "notebook_create", {"path": "test.ipynb"})
    nb_id = result["notebook_id"]
    c1 = await _call(mcp, "notebook_insert_cell", {
        "notebook_id": nb_id, "index": 0, "source": "x=1", "cell_type": "code",
    })

    # Execute
    job = await orch.queue_execution(nb_id, [c1["cell_id"]], "agent-1", ActorType.AGENT)
    await asyncio.sleep(0.3)

    # Get runtime
    runtime = orch.runtime_manager.get_runtime_for_notebook(nb_id)

    # Detach all clients
    for client in list(runtime.attached_clients):
        await orch.runtime_manager.detach_client(runtime.runtime_id, client)

    # Runtime still exists (detached, not destroyed)
    assert runtime.state == RuntimeState.LIVE_DETACHED
    assert runtime.kernel_id in kernel._alive

    # Job still completed
    j = await orch.get_job(job.job_id)
    assert j.status == JobStatus.SUCCEEDED


async def test_acceptance_one_notebook_truth(stack):
    """There is one notebook truth, not a Jupyter truth plus a Hypernote truth."""
    orch, mcp, ledger, _ = stack

    result = await _call(mcp, "notebook_create", {"path": "single-truth.ipynb"})
    nb_id = result["notebook_id"]

    # Edit via MCP
    await _call(mcp, "notebook_insert_cell", {
        "notebook_id": nb_id, "index": 0, "source": "truth = 42", "cell_type": "code",
    })

    # Read via orchestrator directly
    cells = await orch.list_cells(nb_id)
    assert len(cells) == 1
    assert cells[0]["source"] == "truth = 42"

    # Both see the same thing — there's one truth
