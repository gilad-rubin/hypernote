"""Hypernote Jupyter Server extension.

Registers REST handlers for the NotebookControlAPI and wires up
the product thin layer (ActorLedger, RuntimeManager, ExecutionOrchestrator).
"""

from __future__ import annotations

import os

from jupyter_server.extension.application import ExtensionApp

from hypernote.actor_ledger import ActorLedger
from hypernote.runtime_manager import KernelBackend, RuntimeManager, RuntimePolicy
from hypernote.execution_orchestrator import ExecutionOrchestrator
from hypernote.server.handlers import (
    CellAttributionHandler,
    CellHandler,
    CellsHandler,
    InterruptHandler,
    JobHandler,
    JobsHandler,
    NotebookHandler,
    NotebooksHandler,
    RuntimeStatusHandler,
    RuntimeOpenHandler,
    RuntimeStopHandler,
    SendStdinHandler,
    ExecuteHandler,
    SaveHandler,
)
from hypernote.server.jupyter_backends import JupyterKernelBackend, JupyterNotebookBackend


class HypernoteExtension(ExtensionApp):
    name = "hypernote"

    def initialize_settings(self) -> None:
        db_path = os.environ.get("HYPERNOTE_DB_PATH", ":memory:")
        self._ledger = ActorLedger(db_path)
        self._kernel_backend: KernelBackend = JupyterKernelBackend(
            self.settings["kernel_manager"]
        )
        self._runtime_mgr = RuntimeManager(self._kernel_backend, RuntimePolicy())

    async def _ensure_initialized(self) -> None:
        if not hasattr(self, "_orchestrator"):
            await self._ledger.initialize()
            nb_backend = JupyterNotebookBackend(self.serverapp)
            self._orchestrator = ExecutionOrchestrator(
                self._ledger, self._runtime_mgr, _placeholder_exec_backend(), nb_backend
            )
            self.settings["hypernote_orchestrator"] = self._orchestrator

    def initialize_handlers(self) -> None:
        # Lazy initialization: orchestrator is created on first request
        # because some Jupyter services aren't ready during handler registration
        orch_getter = {"get_orchestrator": self._get_orchestrator}

        self.handlers.extend([
            # Notebooks
            (r"/hypernote/api/notebooks", NotebooksHandler, orch_getter),
            (r"/hypernote/api/notebooks/(?P<notebook_id>[^/]+)", NotebookHandler, orch_getter),
            (r"/hypernote/api/notebooks/(?P<notebook_id>[^/]+)/save", SaveHandler, orch_getter),
            # Cells
            (r"/hypernote/api/notebooks/(?P<notebook_id>[^/]+)/cells", CellsHandler, orch_getter),
            (r"/hypernote/api/notebooks/(?P<notebook_id>[^/]+)/cells/(?P<cell_id>[^/]+)", CellHandler, orch_getter),
            (r"/hypernote/api/notebooks/(?P<notebook_id>[^/]+)/cells/(?P<cell_id>[^/]+)/attribution", CellAttributionHandler, orch_getter),
            # Execution
            (r"/hypernote/api/notebooks/(?P<notebook_id>[^/]+)/execute", ExecuteHandler, orch_getter),
            (r"/hypernote/api/jobs", JobsHandler, orch_getter),
            (r"/hypernote/api/jobs/(?P<job_id>[^/]+)", JobHandler, orch_getter),
            (r"/hypernote/api/jobs/(?P<job_id>[^/]+)/stdin", SendStdinHandler, orch_getter),
            # Runtime
            (r"/hypernote/api/notebooks/(?P<notebook_id>[^/]+)/runtime", RuntimeStatusHandler, orch_getter),
            (r"/hypernote/api/notebooks/(?P<notebook_id>[^/]+)/runtime/open", RuntimeOpenHandler, orch_getter),
            (r"/hypernote/api/notebooks/(?P<notebook_id>[^/]+)/runtime/stop", RuntimeStopHandler, orch_getter),
            (r"/hypernote/api/notebooks/(?P<notebook_id>[^/]+)/interrupt", InterruptHandler, orch_getter),
        ])

    async def _get_orchestrator(self) -> ExecutionOrchestrator:
        await self._ensure_initialized()
        return self._orchestrator

    async def stop_extension(self) -> None:
        if hasattr(self, "_runtime_mgr"):
            await self._runtime_mgr.shutdown()
        if hasattr(self, "_ledger"):
            await self._ledger.close()


def _placeholder_exec_backend():
    """Placeholder until wired to jupyter-server-nbmodel's ExecutionStack."""
    from hypernote.execution_orchestrator import ExecutionBackend, ExecutionResult, ExecutionStatus

    class PlaceholderExecBackend(ExecutionBackend):
        async def execute(self, kernel_id, code):
            raise NotImplementedError("Wire to jupyter-server-nbmodel ExecutionStack")

        async def poll_result(self, kernel_id, request_uid):
            return ExecutionResult(status=ExecutionStatus.ERROR, error="Not wired")

        async def send_input(self, kernel_id, value):
            raise NotImplementedError

    return PlaceholderExecBackend()
