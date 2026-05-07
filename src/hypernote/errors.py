"""Public SDK error types."""

from __future__ import annotations


class HypernoteError(Exception):
    """Base class for Hypernote SDK failures."""


class NotebookNotFoundError(HypernoteError, FileNotFoundError):
    """Raised when a notebook path does not exist."""


class CellNotFoundError(HypernoteError, KeyError):
    """Raised when a requested cell id is missing."""


class RuntimeUnavailableError(HypernoteError):
    """Raised when runtime operations cannot be completed."""


class ExecutionTimeoutError(HypernoteError, TimeoutError):
    """Raised when waiting on a job exceeds the caller timeout."""


class InputNotExpectedError(HypernoteError):
    """Raised when stdin is sent to a job that is not awaiting input."""
