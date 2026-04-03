"""Tests for NotebookRoom-based runtime lifecycle."""

from __future__ import annotations

import pytest
from tornado import web

from hypernote.runtime_manager import (
    RuntimeKernelMismatchError,
    RuntimeManager,
    RuntimePolicy,
    RuntimeState,
)


class MockKernelManager:
    def __init__(self):
        self._alive: set[str] = set()
        self.interrupted: list[str] = []

    def __contains__(self, kernel_id: str) -> bool:
        return kernel_id in self._alive

    async def shutdown_kernel(self, kernel_id: str) -> None:
        self._alive.discard(kernel_id)

    async def interrupt_kernel(self, kernel_id: str) -> None:
        if kernel_id not in self._alive:
            raise ValueError(f"Kernel {kernel_id} not alive")
        self.interrupted.append(kernel_id)


class MockSessionManager:
    def __init__(self, kernel_manager: MockKernelManager):
        self._kernel_manager = kernel_manager
        self._sessions: dict[str, dict] = {}
        self._next_id = 0

    async def create_session(
        self,
        path=None,
        name=None,
        type=None,
        kernel_name=None,
        kernel_id=None,
    ):
        self._next_id += 1
        sid = f"session-{self._next_id}"
        kid = kernel_id or f"kernel-{self._next_id}"
        self._kernel_manager._alive.add(kid)
        session = {
            "id": sid,
            "path": path,
            "name": name,
            "type": type,
            "kernel": {"id": kid, "name": kernel_name or "python3"},
        }
        self._sessions[sid] = session
        return session

    async def get_session(self, **kwargs):
        for session in self._sessions.values():
            if all(session.get(key) == value for key, value in kwargs.items()):
                return session
        raise web.HTTPError(404, "Session not found")

    async def list_sessions(self):
        return list(self._sessions.values())

    async def delete_session(self, session_id):
        session = self._sessions.pop(session_id)
        await self._kernel_manager.shutdown_kernel(session["kernel"]["id"])


@pytest.fixture
def kernel_manager():
    return MockKernelManager()


@pytest.fixture
def session_manager(kernel_manager):
    return MockSessionManager(kernel_manager)


@pytest.fixture
def manager(session_manager, kernel_manager):
    return RuntimeManager(session_manager, kernel_manager, RuntimePolicy(idle_ttl_seconds=10))


async def test_open_runtime_attaches_client(manager: RuntimeManager):
    room = await manager.open_runtime("nb-1.ipynb", "client-a")
    assert room.state == RuntimeState.LIVE_ATTACHED
    assert room.kernel_id is not None
    assert "client-a" in room.attached_clients


async def test_ensure_room_without_client_is_detached(manager: RuntimeManager):
    room = await manager.ensure_room("nb-1.ipynb")
    assert room.state == RuntimeState.LIVE_DETACHED
    assert room.attached_clients == set()


async def test_reuse_existing_runtime(manager: RuntimeManager):
    room1 = await manager.open_runtime("nb-1.ipynb", "client-a")
    room2 = await manager.open_runtime("nb-1.ipynb", "client-b")
    assert room1.room_id == room2.room_id
    assert room2.attached_clients == {"client-a", "client-b"}


async def test_ensure_room_rejects_live_kernel_mismatch(manager: RuntimeManager):
    await manager.ensure_room("nb-1.ipynb", kernel_name="python3")

    with pytest.raises(RuntimeKernelMismatchError, match="wants kernel 'other-kernel'"):
        await manager.ensure_room("nb-1.ipynb", kernel_name="other-kernel")


async def test_ensure_room_none_kernel_reuses_existing_without_mismatch(
    manager: RuntimeManager,
):
    await manager.ensure_room("nb-1.ipynb", kernel_name="custom-kernel")
    # Internal callers omit kernel_name — must not raise even for non-python3 rooms
    room = await manager.ensure_room("nb-1.ipynb", kernel_name=None)
    assert room is not None


async def test_detach_transitions_to_live_detached(manager: RuntimeManager):
    room = await manager.open_runtime("nb-1.ipynb", "client-a")
    await manager.detach_client(room.room_id, "client-a")
    assert room.state == RuntimeState.LIVE_DETACHED
    assert room.attached_clients == set()


async def test_stop_runtime_stops_session_and_kernel(
    manager: RuntimeManager,
    kernel_manager: MockKernelManager,
):
    room = await manager.open_runtime("nb-1.ipynb", "client-a")
    kernel_id = room.kernel_id
    await manager.stop_runtime(room.room_id)
    assert room.state == RuntimeState.STOPPED
    assert kernel_id not in kernel_manager._alive


async def test_interrupt_runtime(manager: RuntimeManager, kernel_manager: MockKernelManager):
    room = await manager.open_runtime("nb-1.ipynb", "client-a")
    await manager.interrupt_runtime(room.room_id)
    assert room.kernel_id in kernel_manager.interrupted


async def test_gc_stops_idle_detached(manager: RuntimeManager):
    room = await manager.ensure_room("nb-1.ipynb")
    room.last_activity -= 20
    stopped = await manager.gc_sweep()
    assert room.room_id in stopped
    assert room.state == RuntimeState.STOPPED


async def test_get_runtime_status_for_missing_notebook(manager: RuntimeManager):
    status = await manager.get_runtime_status("missing.ipynb")
    assert status["state"] == RuntimeState.STOPPED.value
    assert status["room_id"] is None


async def test_mark_job_started_and_finished(manager: RuntimeManager):
    room = await manager.ensure_room("nb-1.ipynb")
    manager.mark_job_started("nb-1.ipynb", "job-1")
    assert room.active_jobs == {"job-1"}
    manager.mark_job_finished("nb-1.ipynb", "job-1")
    assert room.active_jobs == set()


async def test_stop_runtime_evicts_notebook_via_callback(
    session_manager: MockSessionManager,
    kernel_manager: MockKernelManager,
):
    evicted: list[str] = []
    manager = RuntimeManager(
        session_manager,
        kernel_manager,
        RuntimePolicy(idle_ttl_seconds=10),
        on_notebook_stopped=evicted.append,
    )

    room = await manager.open_runtime("nb-1.ipynb", "client-a")
    await manager.stop_runtime(room.room_id)

    assert evicted == ["nb-1.ipynb"]
    assert manager.get_room_for_notebook("nb-1.ipynb") is None
