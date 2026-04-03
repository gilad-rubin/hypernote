"""NotebookRoom-based runtime lifecycle management.

Jupyter owns sessions and kernels. Hypernote keeps a thin control plane over
those Jupyter primitives so runtimes have explicit attach/detach/retain/evict
semantics instead of being implicitly owned by a UI tab or CLI process.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

from tornado import web

logger = logging.getLogger(__name__)


class RuntimeKernelMismatchError(RuntimeError):
    """Raised when a live runtime does not match the notebook's desired kernel."""

    def __init__(
        self,
        notebook_id: str,
        *,
        active_kernel_name: str | None,
        requested_kernel_name: str,
    ) -> None:
        active = active_kernel_name or "<unknown>"
        super().__init__(
            "Notebook "
            f"{notebook_id} wants kernel {requested_kernel_name!r}, "
            f"but the live runtime is using {active!r}. "
            "Stop or restart the runtime to pick up the notebook's kernelspec."
        )
        self.notebook_id = notebook_id
        self.active_kernel_name = active_kernel_name
        self.requested_kernel_name = requested_kernel_name


class RuntimeState(str, Enum):
    STARTING = "starting"
    LIVE_ATTACHED = "live-attached"
    LIVE_DETACHED = "live-detached"
    AWAITING_INPUT = "awaiting-input"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class RuntimePolicy:
    idle_ttl_seconds: float = 3600.0
    gc_interval_seconds: float = 60.0


@dataclass
class NotebookRoom:
    room_id: str
    notebook_id: str
    session_id: str | None = None
    kernel_id: str | None = None
    kernel_name: str | None = None
    state: RuntimeState = RuntimeState.STARTING
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    attached_clients: set[str] = field(default_factory=set)
    active_jobs: set[str] = field(default_factory=set)
    job_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @property
    def is_live(self) -> bool:
        return self.state in {
            RuntimeState.LIVE_ATTACHED,
            RuntimeState.LIVE_DETACHED,
            RuntimeState.AWAITING_INPUT,
        }


class RuntimeManager:
    """Thin NotebookRoom registry backed by Jupyter session/kernel services."""

    def __init__(
        self,
        session_manager: Any,
        kernel_manager: Any,
        policy: RuntimePolicy | None = None,
        on_notebook_stopped: Callable[[str], Awaitable[None] | None] | None = None,
    ):
        self._session_manager = session_manager
        self._kernel_manager = kernel_manager
        self._policy = policy or RuntimePolicy()
        self._on_notebook_stopped = on_notebook_stopped
        self._rooms: dict[str, NotebookRoom] = {}
        self._notebook_to_room: dict[str, str] = {}
        self._gc_task: asyncio.Task | None = None

    @property
    def rooms(self) -> dict[str, NotebookRoom]:
        return dict(self._rooms)

    def get_room_for_notebook(self, notebook_id: str) -> NotebookRoom | None:
        room_id = self._notebook_to_room.get(notebook_id)
        if room_id is None:
            return None
        return self._rooms.get(room_id)

    def list_runtimes(self, notebook_id: str | None = None) -> list[NotebookRoom]:
        rooms = self._rooms.values()
        if notebook_id is not None:
            rooms = [room for room in rooms if room.notebook_id == notebook_id]
        return list(rooms)

    async def ensure_room(
        self,
        notebook_id: str,
        kernel_name: str | None = None,
    ) -> NotebookRoom:
        """Ensure a live room exists for ``notebook_id`` without attaching a client."""
        desired_kernel_name = kernel_name or "python3"
        existing = await self._load_or_refresh_room(notebook_id)
        if existing is not None and existing.is_live:
            if (
                kernel_name is not None
                and existing.kernel_name is not None
                and existing.kernel_name != kernel_name
            ):
                raise RuntimeKernelMismatchError(
                    notebook_id,
                    active_kernel_name=existing.kernel_name,
                    requested_kernel_name=kernel_name,
                )
            self.touch_activity(existing.room_id)
            return existing

        room = NotebookRoom(room_id=uuid.uuid4().hex[:12], notebook_id=notebook_id)
        self._rooms[room.room_id] = room
        self._notebook_to_room[notebook_id] = room.room_id

        try:
            session = await self._session_manager.create_session(
                path=notebook_id,
                name=os.path.basename(notebook_id),
                type="notebook",
                kernel_name=desired_kernel_name,
            )
            room.session_id = session["id"]
            room.kernel_id = session["kernel"]["id"]
            room.kernel_name = session["kernel"].get("name")
            room.state = RuntimeState.LIVE_DETACHED
            room.last_activity = time.time()
            logger.info(
                "Opened room %s for notebook %s using kernel %s",
                room.room_id,
                notebook_id,
                room.kernel_id,
            )
            return room
        except Exception:
            room.state = RuntimeState.FAILED
            logger.exception("Failed to open room for notebook %s", notebook_id)
            raise

    async def open_runtime(
        self,
        notebook_id: str,
        client_id: str,
        kernel_name: str | None = None,
    ) -> NotebookRoom:
        """Ensure a live room exists and attach a client to it."""
        room = await self.ensure_room(notebook_id, kernel_name=kernel_name)
        room.attached_clients.add(client_id)
        room.state = RuntimeState.LIVE_ATTACHED
        room.last_activity = time.time()
        return room

    async def attach_client(self, room_id: str, client_id: str) -> NotebookRoom:
        room = self._rooms.get(room_id)
        if room is None:
            raise ValueError(f"Room {room_id} not found")
        if not room.is_live:
            raise ValueError(f"Room {room_id} is {room.state}, cannot attach")
        room.attached_clients.add(client_id)
        room.state = RuntimeState.LIVE_ATTACHED
        room.last_activity = time.time()
        return room

    async def detach_client(self, room_id: str, client_id: str) -> NotebookRoom:
        room = self._rooms.get(room_id)
        if room is None:
            raise ValueError(f"Room {room_id} not found")
        room.attached_clients.discard(client_id)
        if not room.attached_clients and room.state == RuntimeState.LIVE_ATTACHED:
            room.state = RuntimeState.LIVE_DETACHED
        room.last_activity = time.time()
        return room

    async def stop_runtime(self, room_id: str) -> NotebookRoom:
        room = self._rooms.get(room_id)
        if room is None:
            raise ValueError(f"Room {room_id} not found")
        if room.state in {RuntimeState.STOPPED, RuntimeState.STOPPING}:
            return room

        room.state = RuntimeState.STOPPING
        room.last_activity = time.time()

        try:
            if room.session_id is not None:
                await self._session_manager.delete_session(room.session_id)
            elif room.kernel_id is not None and room.kernel_id in self._kernel_manager:
                await self._kernel_manager.shutdown_kernel(room.kernel_id)
        except web.HTTPError:
            logger.warning("Session or kernel already gone for room %s", room.room_id)
        except Exception:
            logger.exception("Failed to stop room %s cleanly", room.room_id)
        finally:
            room.state = RuntimeState.STOPPED
            room.attached_clients.clear()
            room.active_jobs.clear()
            room.last_activity = time.time()
            self._notebook_to_room.pop(room.notebook_id, None)
            self._rooms.pop(room.room_id, None)

        await self._notify_notebook_stopped(room.notebook_id)
        return room

    async def interrupt_runtime(self, room_id: str) -> None:
        room = self._rooms.get(room_id)
        if room is None:
            raise ValueError(f"Room {room_id} not found")
        if not room.kernel_id or not room.is_live:
            raise ValueError(f"Room {room_id} has no live kernel")
        await self._kernel_manager.interrupt_kernel(room.kernel_id)
        room.last_activity = time.time()

    async def get_runtime_status(self, notebook_id: str) -> dict[str, Any]:
        room = await self._load_or_refresh_room(notebook_id)
        if room is None:
            return {
                "state": RuntimeState.STOPPED.value,
                "room_id": None,
                "session_id": None,
                "kernel_id": None,
                "kernel_name": None,
                "attached_clients": [],
                "active_jobs": [],
                "last_activity": None,
                "recoverable": False,
            }

        return {
            "state": room.state.value,
            "room_id": room.room_id,
            "session_id": room.session_id,
            "kernel_id": room.kernel_id,
            "kernel_name": room.kernel_name,
            "attached_clients": sorted(room.attached_clients),
            "active_jobs": sorted(room.active_jobs),
            "last_activity": room.last_activity,
            "recoverable": room.is_live and room.state == RuntimeState.LIVE_DETACHED,
        }

    def set_runtime_state(self, room_id: str, state: RuntimeState) -> None:
        room = self._rooms.get(room_id)
        if room is None:
            raise ValueError(f"Room {room_id} not found")
        room.state = state
        room.last_activity = time.time()

    def touch_activity(self, room_id: str) -> None:
        room = self._rooms.get(room_id)
        if room is not None:
            room.last_activity = time.time()

    def mark_job_started(self, notebook_id: str, job_id: str) -> None:
        room = self.get_room_for_notebook(notebook_id)
        if room is not None:
            room.active_jobs.add(job_id)
            room.last_activity = time.time()

    def mark_job_finished(self, notebook_id: str, job_id: str) -> None:
        room = self.get_room_for_notebook(notebook_id)
        if room is not None:
            room.active_jobs.discard(job_id)
            room.last_activity = time.time()

    async def gc_sweep(self) -> list[str]:
        now = time.time()
        to_stop: list[str] = []
        for room in self._rooms.values():
            if room.state in {
                RuntimeState.STOPPING,
                RuntimeState.STOPPED,
                RuntimeState.FAILED,
            }:
                continue

            idle = (
                room.state == RuntimeState.LIVE_DETACHED
                and now - room.last_activity > self._policy.idle_ttl_seconds
            )
            if idle:
                to_stop.append(room.room_id)

        stopped: list[str] = []
        for room_id in to_stop:
            try:
                await self.stop_runtime(room_id)
                stopped.append(room_id)
            except Exception:
                logger.exception("GC failed to stop room %s", room_id)
        return stopped

    async def start_gc_loop(self) -> None:
        if self._gc_task is not None and not self._gc_task.done():
            return

        async def _gc_loop() -> None:
            while True:
                await asyncio.sleep(self._policy.gc_interval_seconds)
                stopped = await self.gc_sweep()
                if stopped:
                    logger.info("GC stopped %d rooms: %s", len(stopped), stopped)

        self._gc_task = asyncio.create_task(_gc_loop())

    async def stop_gc_loop(self) -> None:
        if self._gc_task is not None and not self._gc_task.done():
            self._gc_task.cancel()
            try:
                await self._gc_task
            except asyncio.CancelledError:
                pass

    async def shutdown(self) -> None:
        await self.stop_gc_loop()
        for room_id in list(self._rooms):
            try:
                await self.stop_runtime(room_id)
            except Exception:
                logger.exception("Failed to stop room %s during shutdown", room_id)

    async def _load_or_refresh_room(self, notebook_id: str) -> NotebookRoom | None:
        room = self.get_room_for_notebook(notebook_id)
        if room is not None:
            if room.kernel_id and room.kernel_id in self._kernel_manager:
                return room
            if room.state not in {RuntimeState.STOPPED, RuntimeState.FAILED}:
                room.state = RuntimeState.STOPPED
            return room

        try:
            session = await self._session_manager.get_session(path=notebook_id)
        except web.HTTPError:
            return None

        room = NotebookRoom(
            room_id=uuid.uuid4().hex[:12],
            notebook_id=notebook_id,
            session_id=session["id"],
            kernel_id=session["kernel"]["id"],
            kernel_name=session["kernel"].get("name"),
            state=RuntimeState.LIVE_DETACHED,
        )
        self._rooms[room.room_id] = room
        self._notebook_to_room[notebook_id] = room.room_id
        return room

    async def _notify_notebook_stopped(self, notebook_id: str) -> None:
        if self._on_notebook_stopped is None:
            return
        try:
            result = self._on_notebook_stopped(notebook_id)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception("Failed to evict notebook state for %s", notebook_id)
