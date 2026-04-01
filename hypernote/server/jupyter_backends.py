"""Concrete Jupyter backend implementations.

Wraps Jupyter Server services for use by RuntimeManager and ExecutionOrchestrator.
"""

from __future__ import annotations

import uuid
from typing import Any

from hypernote.runtime_manager import KernelBackend
from hypernote.execution_orchestrator import NotebookBackend


class JupyterKernelBackend(KernelBackend):
    """Wraps Jupyter Server's MappingKernelManager."""

    def __init__(self, kernel_manager: Any):
        self._km = kernel_manager

    async def start_kernel(self, kernel_name: str = "python3") -> tuple[str, str]:
        kernel_id = await self._km.start_kernel(kernel_name=kernel_name)
        session_id = uuid.uuid4().hex
        return kernel_id, session_id

    async def shutdown_kernel(self, kernel_id: str) -> None:
        await self._km.shutdown_kernel(kernel_id)

    async def interrupt_kernel(self, kernel_id: str) -> None:
        await self._km.interrupt_kernel(kernel_id)

    async def is_kernel_alive(self, kernel_id: str) -> bool:
        return await self._km.is_alive(kernel_id)


class JupyterNotebookBackend(NotebookBackend):
    """Wraps Jupyter Server's contents manager for notebook operations.

    In the full product, this would operate on the Y.js shared notebook model
    via jupyter-collaboration. For now, wraps the contents API as a baseline.
    """

    def __init__(self, serverapp: Any):
        self._app = serverapp

    @property
    def _contents(self):
        return self._app.contents_manager

    async def create_notebook(self, path: str) -> str:
        model = self._contents.new_untitled(path="", type="notebook", ext=".ipynb")
        return model["path"]

    async def open_notebook(self, path: str) -> str:
        model = self._contents.get(path, content=False)
        return model["path"]

    async def save_notebook(self, notebook_id: str) -> None:
        model = self._contents.get(notebook_id)
        self._contents.save(model, notebook_id)

    async def list_cells(self, notebook_id: str) -> list[dict[str, Any]]:
        model = self._contents.get(notebook_id, content=True)
        nb = model["content"]
        cells = []
        for i, cell in enumerate(nb.get("cells", [])):
            cells.append({
                "id": cell.get("id", f"cell-{i}"),
                "type": cell.get("cell_type", "code"),
                "source": cell.get("source", ""),
                "index": i,
            })
        return cells

    async def get_cell_source(self, notebook_id: str, cell_id: str) -> str:
        cells = await self.list_cells(notebook_id)
        for cell in cells:
            if cell["id"] == cell_id:
                return cell["source"]
        raise ValueError(f"Cell {cell_id} not found in {notebook_id}")

    async def insert_cell(
        self, notebook_id: str, index: int, cell_type: str, source: str
    ) -> str:
        model = self._contents.get(notebook_id, content=True)
        nb = model["content"]
        cells = nb.setdefault("cells", [])
        cell_id = uuid.uuid4().hex[:8]
        new_cell = {
            "id": cell_id,
            "cell_type": cell_type,
            "source": source,
            "metadata": {},
        }
        if cell_type == "code":
            new_cell["outputs"] = []
            new_cell["execution_count"] = None
        cells.insert(index, new_cell)
        self._contents.save(model, notebook_id)
        return cell_id

    async def replace_cell_source(
        self, notebook_id: str, cell_id: str, source: str
    ) -> None:
        model = self._contents.get(notebook_id, content=True)
        nb = model["content"]
        for cell in nb.get("cells", []):
            if cell.get("id") == cell_id:
                cell["source"] = source
                self._contents.save(model, notebook_id)
                return
        raise ValueError(f"Cell {cell_id} not found in {notebook_id}")

    async def delete_cell(self, notebook_id: str, cell_id: str) -> None:
        model = self._contents.get(notebook_id, content=True)
        nb = model["content"]
        nb["cells"] = [c for c in nb.get("cells", []) if c.get("id") != cell_id]
        self._contents.save(model, notebook_id)
