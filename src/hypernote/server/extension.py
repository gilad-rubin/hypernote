"""Hypernote Jupyter Server extension."""

from __future__ import annotations

import asyncio

from jupyter_server.extension.application import ExtensionApp
from tornado.web import URLSpec

from hypernote.actor_ledger import MemoryLedger
from hypernote.execution_orchestrator import ExecutionOrchestrator, SharedNotebookAccessor
from hypernote.runtime_manager import RuntimeManager, RuntimePolicy
from hypernote.server.handlers import (
    CellAttributionHandler,
    ExecuteHandler,
    InterruptHandler,
    JobHandler,
    JobsHandler,
    KernelInterruptInterceptHandler,
    KernelRestartInterceptHandler,
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
from hypernote.server.subshell import validate_nbmodel_internals

NBMODEL_EXTENSION_NAME = "jupyter_server_nbmodel"
YDOC_EXTENSION_NAME = "jupyter_server_ydoc"


class HypernoteExtension(ExtensionApp):
    name = "hypernote"

    def initialize_settings(self) -> None:
        self._ledger = MemoryLedger()
        # Re-entrancy guard for `_ensure_initialized`. Tornado is
        # single-threaded but coroutines interleave at await points; this
        # event ensures concurrent first-time callers all wait for the
        # single in-flight initialization rather than each running it.
        self._init_event: asyncio.Event | None = None

    async def _ensure_initialized(self) -> None:
        if hasattr(self, "_orchestrator"):
            return
        if self._init_event is not None:
            await self._init_event.wait()
            return

        self._init_event = asyncio.Event()
        try:
            await self._ledger.initialize()
            nbmodel_ext = _get_extension_instance(self.serverapp, NBMODEL_EXTENSION_NAME)
            execution_stack = getattr(nbmodel_ext, "_Extension__execution_stack")
            validate_nbmodel_internals(execution_stack)
            ydoc_ext = _get_optional_extension_instance(self.serverapp, YDOC_EXTENSION_NAME)

            runtime_mgr = RuntimeManager(
                session_manager=self.settings["session_manager"],
                kernel_manager=self.settings["kernel_manager"],
                policy=RuntimePolicy(),
                on_notebook_stopped=self._ledger.evict_notebook,
            )
            await runtime_mgr.start_gc_loop()

            notebook_accessor = SharedNotebookAccessor(
                ydoc_ext, self.serverapp.contents_manager
            )
            self._runtime_mgr = runtime_mgr
            self._orchestrator = ExecutionOrchestrator(
                self._ledger,
                runtime_mgr,
                execution_stack,
                notebook_accessor,
            )
            self.settings["hypernote_orchestrator"] = self._orchestrator

            # Override Jupyter Server's default kernel-interrupt + restart
            # routes so Lab's Stop / Restart buttons reach Hypernote-driven
            # cells. Insert at index 0 of the wildcard router so our rule
            # matches before the default handler.
            self._install_interrupt_intercept()
        finally:
            self._init_event.set()

    def _install_interrupt_intercept(self) -> None:
        web_app = self.serverapp.web_app
        kwargs = {"get_orchestrator": self._get_orchestrator}
        # Order matters: insert at index 0 so our routes win over Jupyter
        # Server's default `/api/kernels/{id}/(restart|interrupt)` handler.
        rules = [
            URLSpec(
                r"/api/kernels/(?P<kernel_id>[^/]+)/interrupt",
                KernelInterruptInterceptHandler,
                kwargs,
            ),
            URLSpec(
                r"/api/kernels/(?P<kernel_id>[^/]+)/restart",
                KernelRestartInterceptHandler,
                kwargs,
            ),
        ]
        for rule in rules:
            web_app.wildcard_router.rules.insert(0, rule)

        self._verify_route_overrides()

    def _verify_route_overrides(self) -> None:
        """Fail loud at startup if our route overrides aren't actually winning.

        We rely on inserting at the front of `wildcard_router.rules` to beat
        Jupyter Server's default `/api/kernels/{id}/(restart|interrupt)`
        handler. If a future Jupyter Server change moves default routes
        into a router we don't shadow, our overrides quietly stop working
        and Lab's Stop / Restart buttons silently regress. Walk the router
        rules in order, find the first one whose URL pattern matches each
        probe path, and warn loudly if it isn't ours.
        """
        web_app = self.serverapp.web_app
        probes = (
            ("/api/kernels/test-kernel-probe/interrupt", KernelInterruptInterceptHandler),
            ("/api/kernels/test-kernel-probe/restart", KernelRestartInterceptHandler),
        )
        for path, expected_cls in probes:
            matched_handler = _first_matching_handler(web_app.wildcard_router.rules, path)
            if matched_handler is not expected_cls:
                self.log.warning(
                    "Hypernote route override for %s did not resolve to %s "
                    "(got %r). Lab Stop/Restart against Hypernote-driven "
                    "cells may silently fall back to default handling.",
                    path,
                    expected_cls.__name__,
                    matched_handler,
                )

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


def _first_matching_handler(rules, path: str):
    """Return the handler class of the first router rule whose pattern matches `path`.

    Reads `rule.matcher.regex` and `rule.target` directly. These are
    Tornado-internal attributes (no public stability guarantee), so a
    Tornado upgrade that renames or restructures them would silently
    disable `_verify_route_overrides` (it would log spurious warnings
    without breaking actual routing). If you upgrade Tornado past the
    pinned version, sanity-check this helper still resolves the routes
    it should — see `_verify_route_overrides`.
    """
    import re as _re

    for rule in rules:
        matcher = getattr(rule, "matcher", None)
        regex = getattr(matcher, "regex", None)
        if regex is None:
            continue
        try:
            if regex.match(path) is not None:
                return getattr(rule, "target", None)
        except (TypeError, _re.error):
            continue
    return None


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
