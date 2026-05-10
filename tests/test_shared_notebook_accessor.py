"""Tests for Hypernote's shared-document notebook accessor."""

from __future__ import annotations

import pytest

from hypernote.execution_orchestrator import SharedNotebookAccessor


class _ContentsWithStaleCell:
    def __init__(self) -> None:
        self.get_calls = 0

    def get(self, notebook_id: str, *, content: bool = True) -> dict:  # noqa: ARG002
        self.get_calls += 1
        return {
            "path": notebook_id,
            "type": "notebook",
            "content": {
                "cells": [
                    {
                        "id": "stale-cell",
                        "cell_type": "code",
                        "source": "print('stale')",
                    }
                ]
            },
        }


@pytest.mark.asyncio
async def test_cell_source_comes_from_shared_document_not_file_fallback(monkeypatch):
    contents = _ContentsWithStaleCell()
    accessor = SharedNotebookAccessor(object(), contents)

    async def ensure_document_room(notebook_id: str) -> str:  # noqa: ARG001
        return "room-id"

    async def get_ycell(notebook_id: str, cell_id: str):  # noqa: ANN202, ARG001
        return None

    monkeypatch.setattr(accessor, "ensure_document_room", ensure_document_room)
    monkeypatch.setattr(accessor, "get_ycell", get_ycell)

    with pytest.raises(ValueError, match="stale-cell"):
        await accessor.get_cell_source("demo.ipynb", "stale-cell")
    assert contents.get_calls == 0
