"""Hypernote Jupyter Server extension."""

from __future__ import annotations

import os

from jupyter_server.extension.application import ExtensionApp

from hypernote.actor_ledger import ActorLedger
from hypernote.execution_orchestrator import ExecutionOrchestrator, SharedNotebookAccessor
from hypernote.runtime_manager import RuntimeManager, RuntimePolicy
from hypernote.server.handlers import (
    CellAttributionHandler,
    ExecuteHandler,
    InterruptHandler,
    JobHandler,
    JobsHandler,
    NotebookCellClearOutputsHandler,
    NotebookCellHandler,
    NotebookCellMoveHandler,
    NotebookCellsHandler,
    NotebookDocumentHandler,
    RuntimeOpenHandler,
    RuntimeStatusHandler,
    RuntimeStopHandler,
    SendStdinHandler,
)

NBMODEL_EXTENSION_NAME = "jupyter_server_nbmodel"
YDOC_EXTENSION_NAME = "jupyter_server_ydoc"


class HypernoteExtension(ExtensionApp):
    name = "hypernote"

    def initialize_settings(self) -> None:
        db_path = os.environ.get("HYPERNOTE_DB_PATH", ":memory:")
        self._ledger = ActorLedger(db_path)

    async def _ensure_initialized(self) -> None:
        if hasattr(self, "_orchestrator"):
            return

        await self._ledger.initialize()
        nbmodel_ext = _get_extension_instance(self.serverapp, NBMODEL_EXTENSION_NAME)
        execution_stack = getattr(nbmodel_ext, "_Extension__execution_stack")
        ydoc_ext = _get_optional_extension_instance(self.serverapp, YDOC_EXTENSION_NAME)

        runtime_mgr = RuntimeManager(
            session_manager=self.settings["session_manager"],
            kernel_manager=self.settings["kernel_manager"],
            policy=RuntimePolicy(),
        )
        await runtime_mgr.start_gc_loop()

        notebook_accessor = SharedNotebookAccessor(ydoc_ext, self.serverapp.contents_manager)
        self._runtime_mgr = runtime_mgr
        self._orchestrator = ExecutionOrchestrator(
            self._ledger,
            runtime_mgr,
            execution_stack,
            notebook_accessor,
        )
        self.settings["hypernote_orchestrator"] = self._orchestrator

    def initialize_handlers(self) -> None:
        kwargs = {"get_orchestrator": self._get_orchestrator}
        self.handlers.extend(
            [
                (
                    r"/hypernote/api/notebooks/(?P<notebook_id>.+)/document",
                    NotebookDocumentHandler,
                    kwargs,
                ),
                (
                    r"/hypernote/api/notebooks/(?P<notebook_id>.+)/cells",
                    NotebookCellsHandler,
                    kwargs,
                ),
                (
                    r"/hypernote/api/notebooks/(?P<notebook_id>.+)/cells/(?P<cell_id>[^/]+)",
                    NotebookCellHandler,
                    kwargs,
                ),
                (
                    r"/hypernote/api/notebooks/(?P<notebook_id>.+)/cells/(?P<cell_id>[^/]+)/move",
                    NotebookCellMoveHandler,
                    kwargs,
                ),
                (
                    r"/hypernote/api/notebooks/(?P<notebook_id>.+)/cells/(?P<cell_id>[^/]+)/clear-outputs",
                    NotebookCellClearOutputsHandler,
                    kwargs,
                ),
                (r"/hypernote/api/notebooks/(?P<notebook_id>.+)/execute", ExecuteHandler, kwargs),
                (r"/hypernote/api/jobs", JobsHandler, kwargs),
                (r"/hypernote/api/jobs/(?P<job_id>[^/]+)", JobHandler, kwargs),
                (r"/hypernote/api/jobs/(?P<job_id>[^/]+)/stdin", SendStdinHandler, kwargs),
                (
                    r"/hypernote/api/notebooks/(?P<notebook_id>.+)/runtime",
                    RuntimeStatusHandler,
                    kwargs,
                ),
                (
                    r"/hypernote/api/notebooks/(?P<notebook_id>.+)/runtime/open",
                    RuntimeOpenHandler,
                    kwargs,
                ),
                (
                    r"/hypernote/api/notebooks/(?P<notebook_id>.+)/runtime/stop",
                    RuntimeStopHandler,
                    kwargs,
                ),
                (
                    r"/hypernote/api/notebooks/(?P<notebook_id>.+)/interrupt",
                    InterruptHandler,
                    kwargs,
                ),
                (
                    r"/hypernote/api/notebooks/"
                    r"(?P<notebook_id>.+)/cells/(?P<cell_id>[^/]+)/attribution",
                    CellAttributionHandler,
                    kwargs,
                ),
            ]
        )

    async def _get_orchestrator(self) -> ExecutionOrchestrator:
        await self._ensure_initialized()
        return self._orchestrator

    async def stop_extension(self) -> None:
        if hasattr(self, "_runtime_mgr"):
            await self._runtime_mgr.shutdown()
        if hasattr(self, "_ledger"):
            await self._ledger.close()


def _get_extension_instance(serverapp, extension_name: str):
    exts = serverapp.extension_manager.extension_apps.get(extension_name, set())
    if not exts:
        raise RuntimeError(f"Required Jupyter extension '{extension_name}' is not loaded")
    return next(iter(exts))


def _get_optional_extension_instance(serverapp, extension_name: str):
    exts = serverapp.extension_manager.extension_apps.get(extension_name, set())
    if not exts:
        return None
    return next(iter(exts))


def _jupyter_server_extension_points():
    return [{"module": "hypernote.server.extension", "app": HypernoteExtension}]


load_jupyter_server_extension = HypernoteExtension.load_classic_server_extension
_load_jupyter_server_extension = HypernoteExtension.load_classic_server_extension
