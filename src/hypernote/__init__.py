"""Hypernote: server-owned notebook execution with actor attribution."""

__version__ = "0.1.2"

from hypernote.errors import (
    CellNotFoundError,
    ExecutionTimeoutError,
    HypernoteError,
    InputNotExpectedError,
    NotebookNotFoundError,
    RuntimeUnavailableError,
)
from hypernote.sdk import (
    CellCollection,
    CellHandle,
    CellStatus,
    CellType,
    ChangeKind,
    Job,
    JobStatus,
    Notebook,
    NotebookStatus,
    Runtime,
    RuntimeStatus,
    Snapshot,
    connect,
)
from hypernote.server.extension import HypernoteExtension


def _jupyter_server_extension_points():
    return [{"module": "hypernote", "app": HypernoteExtension}]


load_jupyter_server_extension = HypernoteExtension.load_classic_server_extension
_load_jupyter_server_extension = HypernoteExtension.load_classic_server_extension

__all__ = [
    "connect",
    "Notebook",
    "CellCollection",
    "CellHandle",
    "Runtime",
    "Job",
    "Snapshot",
    "NotebookStatus",
    "CellStatus",
    "CellType",
    "RuntimeStatus",
    "JobStatus",
    "ChangeKind",
    "HypernoteError",
    "NotebookNotFoundError",
    "CellNotFoundError",
    "RuntimeUnavailableError",
    "ExecutionTimeoutError",
    "InputNotExpectedError",
]
