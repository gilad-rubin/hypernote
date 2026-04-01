"""RuntimeManager: server-side runtime lifecycle management.

Answers the question: if truth lives in neither a CLI nor a browser tab,
who owns runtime start, attach, detach, stop, and garbage collection?

Answer: a workspace-scoped server-side runtime manager.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class RuntimeState(str, Enum):
    STARTING = "starting"
    LIVE_ATTACHED = "live-attached"
    LIVE_DETACHED = "live-detached"
    AWAITING_INPUT = "awaiting-input"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class RuntimeInfo:
    runtime_id: str
    notebook_id: str
    kernel_id: str | None = None
    session_id: str | None = None
    state: RuntimeState = RuntimeState.STARTING
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    attached_clients: set[str] = field(default_factory=set)
    pinned: bool = False

    @property
    def is_live(self) -> bool:
        return self.state in (
            RuntimeState.LIVE_ATTACHED,
            RuntimeState.LIVE_DETACHED,
            RuntimeState.AWAITING_INPUT,
        )


@dataclass
class RuntimePolicy:
    idle_ttl_seconds: float = 3600.0  # 1 hour
    max_age_seconds: float | None = None
    gc_interval_seconds: float = 60.0


class KernelBackend:
    """Interface to Jupyter kernel/session management.

    In production, wraps jupyter_client or Jupyter Server's kernel manager.
    For testing, use MockKernelBackend.
    """

    async def start_kernel(self, kernel_name: str = "python3") -> tuple[str, str]:
        """Start a kernel, return (kernel_id, session_id)."""
        raise NotImplementedError

    async def shutdown_kernel(self, kernel_id: str) -> None:
        """Shutdown a kernel."""
        raise NotImplementedError

    async def interrupt_kernel(self, kernel_id: str) -> None:
        """Interrupt a running kernel."""
        raise NotImplementedError

    async def is_kernel_alive(self, kernel_id: str) -> bool:
        """Check if a kernel is still responsive."""
        raise NotImplementedError


class RuntimeManager:
    """Server-side runtime lifecycle manager.

    Owns runtime creation, client attach/detach, liveness, and GC.
    Clients acquire attachments, not ownership. Closing a UI or CLI
    detaches the client but does not kill the runtime.
    """

    def __init__(
        self,
        kernel_backend: KernelBackend,
        policy: RuntimePolicy | None = None,
    ):
        self._backend = kernel_backend
        self._policy = policy or RuntimePolicy()
        self._runtimes: dict[str, RuntimeInfo] = {}
        self._notebook_to_runtime: dict[str, str] = {}
        self._gc_task: asyncio.Task | None = None

    @property
    def runtimes(self) -> dict[str, RuntimeInfo]:
        return dict(self._runtimes)

    def get_runtime_for_notebook(self, notebook_id: str) -> RuntimeInfo | None:
        runtime_id = self._notebook_to_runtime.get(notebook_id)
        if runtime_id is None:
            return None
        return self._runtimes.get(runtime_id)

    async def open_runtime(
        self,
        notebook_id: str,
        client_id: str,
        kernel_name: str = "python3",
    ) -> RuntimeInfo:
        """Open or reuse a runtime for a notebook. Attaches the client."""
        existing = self.get_runtime_for_notebook(notebook_id)
        if existing and existing.is_live:
            return await self.attach_client(existing.runtime_id, client_id)

        runtime_id = uuid.uuid4().hex[:12]
        info = RuntimeInfo(
            runtime_id=runtime_id,
            notebook_id=notebook_id,
        )
        self._runtimes[runtime_id] = info
        self._notebook_to_runtime[notebook_id] = runtime_id

        try:
            kernel_id, session_id = await self._backend.start_kernel(kernel_name)
            info.kernel_id = kernel_id
            info.session_id = session_id
            info.state = RuntimeState.LIVE_ATTACHED
            info.attached_clients.add(client_id)
            info.last_activity = time.time()
            logger.info("Runtime %s started for notebook %s (kernel %s)", runtime_id, notebook_id, kernel_id)
        except Exception:
            info.state = RuntimeState.FAILED
            logger.exception("Failed to start runtime %s", runtime_id)
            raise

        return info

    async def attach_client(self, runtime_id: str, client_id: str) -> RuntimeInfo:
        """Attach a client to an existing runtime."""
        info = self._runtimes.get(runtime_id)
        if info is None:
            raise ValueError(f"Runtime {runtime_id} not found")
        if not info.is_live:
            raise ValueError(f"Runtime {runtime_id} is {info.state}, cannot attach")

        info.attached_clients.add(client_id)
        if info.state == RuntimeState.LIVE_DETACHED:
            info.state = RuntimeState.LIVE_ATTACHED
        info.last_activity = time.time()
        logger.info("Client %s attached to runtime %s", client_id, runtime_id)
        return info

    async def detach_client(self, runtime_id: str, client_id: str) -> RuntimeInfo:
        """Detach a client. Does NOT stop the runtime."""
        info = self._runtimes.get(runtime_id)
        if info is None:
            raise ValueError(f"Runtime {runtime_id} not found")

        info.attached_clients.discard(client_id)
        if not info.attached_clients and info.state == RuntimeState.LIVE_ATTACHED:
            info.state = RuntimeState.LIVE_DETACHED
        info.last_activity = time.time()
        logger.info("Client %s detached from runtime %s", client_id, runtime_id)
        return info

    async def stop_runtime(self, runtime_id: str) -> RuntimeInfo:
        """Explicitly stop a runtime. Shuts down the kernel."""
        info = self._runtimes.get(runtime_id)
        if info is None:
            raise ValueError(f"Runtime {runtime_id} not found")
        if info.state in (RuntimeState.STOPPED, RuntimeState.STOPPING):
            return info

        info.state = RuntimeState.STOPPING
        if info.kernel_id:
            try:
                await self._backend.shutdown_kernel(info.kernel_id)
            except Exception:
                logger.exception("Error shutting down kernel %s", info.kernel_id)
        info.state = RuntimeState.STOPPED
        info.attached_clients.clear()
        logger.info("Runtime %s stopped", runtime_id)
        return info

    async def interrupt_runtime(self, runtime_id: str) -> None:
        """Interrupt the kernel for a runtime."""
        info = self._runtimes.get(runtime_id)
        if info is None:
            raise ValueError(f"Runtime {runtime_id} not found")
        if not info.kernel_id or not info.is_live:
            raise ValueError(f"Runtime {runtime_id} has no live kernel")
        await self._backend.interrupt_kernel(info.kernel_id)

    def set_runtime_state(self, runtime_id: str, state: RuntimeState) -> None:
        """Update runtime state (used by ExecutionOrchestrator for awaiting_input, etc.)."""
        info = self._runtimes.get(runtime_id)
        if info is None:
            raise ValueError(f"Runtime {runtime_id} not found")
        info.state = state
        info.last_activity = time.time()

    def touch_activity(self, runtime_id: str) -> None:
        """Record activity on a runtime (resets idle timer)."""
        info = self._runtimes.get(runtime_id)
        if info:
            info.last_activity = time.time()

    def pin_runtime(self, runtime_id: str, pinned: bool = True) -> None:
        """Pin a runtime to prevent idle GC."""
        info = self._runtimes.get(runtime_id)
        if info:
            info.pinned = pinned

    # --- Garbage collection ---

    async def gc_sweep(self) -> list[str]:
        """Sweep and stop idle/expired runtimes. Returns stopped runtime IDs."""
        now = time.time()
        to_stop = []

        for rid, info in self._runtimes.items():
            if info.pinned or info.state in (RuntimeState.STOPPED, RuntimeState.STOPPING, RuntimeState.FAILED):
                continue

            idle = info.state == RuntimeState.LIVE_DETACHED and (
                now - info.last_activity > self._policy.idle_ttl_seconds
            )
            expired = self._policy.max_age_seconds and (
                now - info.created_at > self._policy.max_age_seconds
            )
            if idle or expired:
                to_stop.append(rid)

        stopped = []
        for rid in to_stop:
            try:
                await self.stop_runtime(rid)
                stopped.append(rid)
            except Exception:
                logger.exception("GC failed to stop runtime %s", rid)

        return stopped

    async def start_gc_loop(self) -> None:
        """Start periodic garbage collection."""
        if self._gc_task and not self._gc_task.done():
            return

        async def _gc_loop():
            while True:
                await asyncio.sleep(self._policy.gc_interval_seconds)
                stopped = await self.gc_sweep()
                if stopped:
                    logger.info("GC stopped %d runtimes: %s", len(stopped), stopped)

        self._gc_task = asyncio.create_task(_gc_loop())

    async def stop_gc_loop(self) -> None:
        if self._gc_task and not self._gc_task.done():
            self._gc_task.cancel()
            try:
                await self._gc_task
            except asyncio.CancelledError:
                pass

    async def shutdown(self) -> None:
        """Stop all runtimes and GC loop."""
        await self.stop_gc_loop()
        for rid in list(self._runtimes):
            try:
                await self.stop_runtime(rid)
            except Exception:
                pass

    def list_runtimes(self, notebook_id: str | None = None) -> list[RuntimeInfo]:
        runtimes = self._runtimes.values()
        if notebook_id:
            runtimes = [r for r in runtimes if r.notebook_id == notebook_id]
        return list(runtimes)
