"""Tests for RuntimeManager — server-side runtime lifecycle."""

import pytest

from hypernote.runtime_manager import (
    KernelBackend,
    RuntimeManager,
    RuntimePolicy,
    RuntimeState,
)


class MockKernelBackend(KernelBackend):
    def __init__(self):
        self._next_id = 0
        self._alive: set[str] = set()

    async def start_kernel(self, kernel_name: str = "python3") -> tuple[str, str]:
        self._next_id += 1
        kid = f"kernel-{self._next_id}"
        sid = f"session-{self._next_id}"
        self._alive.add(kid)
        return kid, sid

    async def shutdown_kernel(self, kernel_id: str) -> None:
        self._alive.discard(kernel_id)

    async def interrupt_kernel(self, kernel_id: str) -> None:
        if kernel_id not in self._alive:
            raise ValueError(f"Kernel {kernel_id} not alive")

    async def is_kernel_alive(self, kernel_id: str) -> bool:
        return kernel_id in self._alive


@pytest.fixture
def backend():
    return MockKernelBackend()


@pytest.fixture
def manager(backend):
    return RuntimeManager(backend, RuntimePolicy(idle_ttl_seconds=10))


async def test_open_runtime(manager: RuntimeManager):
    info = await manager.open_runtime("nb-1", "client-a")
    assert info.state == RuntimeState.LIVE_ATTACHED
    assert info.kernel_id is not None
    assert "client-a" in info.attached_clients


async def test_reuse_existing_runtime(manager: RuntimeManager):
    info1 = await manager.open_runtime("nb-1", "client-a")
    info2 = await manager.open_runtime("nb-1", "client-b")
    assert info1.runtime_id == info2.runtime_id
    assert "client-a" in info2.attached_clients
    assert "client-b" in info2.attached_clients


async def test_detach_transitions_to_live_detached(manager: RuntimeManager):
    info = await manager.open_runtime("nb-1", "client-a")
    await manager.detach_client(info.runtime_id, "client-a")
    assert info.state == RuntimeState.LIVE_DETACHED
    assert not info.attached_clients


async def test_detach_does_not_stop_runtime(manager: RuntimeManager, backend: MockKernelBackend):
    info = await manager.open_runtime("nb-1", "client-a")
    kid = info.kernel_id
    await manager.detach_client(info.runtime_id, "client-a")
    assert info.state == RuntimeState.LIVE_DETACHED
    assert kid in backend._alive


async def test_reattach_after_detach(manager: RuntimeManager):
    info = await manager.open_runtime("nb-1", "client-a")
    await manager.detach_client(info.runtime_id, "client-a")
    assert info.state == RuntimeState.LIVE_DETACHED

    info2 = await manager.attach_client(info.runtime_id, "client-b")
    assert info2.state == RuntimeState.LIVE_ATTACHED
    assert "client-b" in info2.attached_clients


async def test_stop_runtime(manager: RuntimeManager, backend: MockKernelBackend):
    info = await manager.open_runtime("nb-1", "client-a")
    kid = info.kernel_id
    await manager.stop_runtime(info.runtime_id)
    assert info.state == RuntimeState.STOPPED
    assert kid not in backend._alive
    assert not info.attached_clients


async def test_interrupt_runtime(manager: RuntimeManager):
    info = await manager.open_runtime("nb-1", "client-a")
    await manager.interrupt_runtime(info.runtime_id)  # should not raise


async def test_gc_stops_idle_detached(manager: RuntimeManager, backend: MockKernelBackend):
    info = await manager.open_runtime("nb-1", "client-a")
    await manager.detach_client(info.runtime_id, "client-a")

    # Simulate time passing beyond idle TTL
    info.last_activity -= 20  # 20 seconds ago, TTL is 10
    stopped = await manager.gc_sweep()
    assert info.runtime_id in stopped
    assert info.state == RuntimeState.STOPPED


async def test_gc_skips_pinned(manager: RuntimeManager):
    info = await manager.open_runtime("nb-1", "client-a")
    await manager.detach_client(info.runtime_id, "client-a")
    manager.pin_runtime(info.runtime_id)

    info.last_activity -= 20
    stopped = await manager.gc_sweep()
    assert not stopped
    assert info.state == RuntimeState.LIVE_DETACHED


async def test_gc_skips_attached(manager: RuntimeManager):
    info = await manager.open_runtime("nb-1", "client-a")
    info.last_activity -= 20  # Old activity but still attached
    stopped = await manager.gc_sweep()
    assert not stopped


async def test_list_runtimes(manager: RuntimeManager):
    await manager.open_runtime("nb-1", "c1")
    await manager.open_runtime("nb-2", "c2")
    assert len(manager.list_runtimes()) == 2
    assert len(manager.list_runtimes(notebook_id="nb-1")) == 1


async def test_set_runtime_state(manager: RuntimeManager):
    info = await manager.open_runtime("nb-1", "client-a")
    manager.set_runtime_state(info.runtime_id, RuntimeState.AWAITING_INPUT)
    assert info.state == RuntimeState.AWAITING_INPUT
    assert info.is_live


async def test_shutdown_stops_all(manager: RuntimeManager, backend: MockKernelBackend):
    await manager.open_runtime("nb-1", "c1")
    await manager.open_runtime("nb-2", "c2")
    await manager.shutdown()
    assert all(r.state == RuntimeState.STOPPED for r in manager.runtimes.values())
