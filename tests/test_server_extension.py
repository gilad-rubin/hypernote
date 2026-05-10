"""Tests for Hypernote server extension startup behavior."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hypernote.server import extension as extension_mod
from hypernote.server.extension import (
    NBMODEL_EXTENSION_NAME,
    HypernoteExtension,
)


class _FakeLedger:
    async def initialize(self) -> None:
        return None

    async def evict_notebook(self, notebook_id: str) -> None:  # noqa: ARG002
        return None


class _FakeRuntimeManager:
    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None

    async def start_gc_loop(self) -> None:
        return None


class _FakeNbmodelExtension:
    pass


@pytest.mark.asyncio
async def test_extension_startup_requires_shared_document_extension(monkeypatch):
    fake_nbmodel = _FakeNbmodelExtension()
    fake_nbmodel._Extension__execution_stack = object()
    ext = HypernoteExtension.__new__(HypernoteExtension)
    ext._ledger = _FakeLedger()
    ext._init_event = None
    ext.settings = {"session_manager": object(), "kernel_manager": object()}
    ext.serverapp = SimpleNamespace(
        extension_manager=SimpleNamespace(
            extension_apps={NBMODEL_EXTENSION_NAME: {fake_nbmodel}},
        ),
        contents_manager=object(),
    )

    monkeypatch.setattr(extension_mod, "RuntimeManager", _FakeRuntimeManager)
    monkeypatch.setattr(HypernoteExtension, "_install_interrupt_intercept", lambda self: None)

    with pytest.raises(RuntimeError, match="jupyter_server_ydoc"):
        await ext._ensure_initialized()
