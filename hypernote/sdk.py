"""Synchronous user-facing Hypernote SDK."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.parse
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Iterator

import httpx

from hypernote.errors import (
    CellNotFoundError,
    ExecutionTimeoutError,
    HypernoteError,
    InputNotExpectedError,
    NotebookNotFoundError,
    RuntimeUnavailableError,
)

SUMMARY_SOURCE_CHARS = 120
SUMMARY_OUTPUT_TEXT_CHARS = 80


class CellType(str, Enum):
    CODE = "code"
    MARKDOWN = "markdown"
    RAW = "raw"


class RuntimeStatus(str, Enum):
    STARTING = "starting"
    LIVE_ATTACHED = "live-attached"
    LIVE_DETACHED = "live-detached"
    AWAITING_INPUT = "awaiting-input"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class ChangeKind(str, Enum):
    ADDED = "added"
    DELETED = "deleted"
    MOVED = "moved"
    SOURCE_EDITED = "source_edited"
    OUTPUT_CHANGED = "output_changed"
    EXECUTION_COUNT = "execution_count"


@dataclass(frozen=True)
class Snapshot:
    token: str
    timestamp: float
    cell_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CellStatus:
    id: str
    type: CellType
    changed: bool
    change_kinds: tuple[ChangeKind, ...]
    source: str | None
    outputs: tuple[dict[str, Any], ...] | None
    execution_count: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "changed": self.changed,
            "change_kinds": [kind.value for kind in self.change_kinds],
            "source": self.source,
            "outputs": list(self.outputs) if self.outputs is not None else None,
            "execution_count": self.execution_count,
        }


@dataclass(frozen=True)
class NotebookStatus:
    notebook_path: str
    baseline: Snapshot | None
    current: Snapshot
    runtime: RuntimeStatus
    cells: tuple[CellStatus, ...]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "notebook_path": self.notebook_path,
            "baseline": self.baseline.to_dict() if self.baseline is not None else None,
            "current": self.current.to_dict(),
            "runtime": self.runtime.value,
            "cells": [cell.to_dict() for cell in self.cells],
            "summary": self.summary,
        }


@dataclass(frozen=True)
class _SnapshotCell:
    id: str
    type: str
    order: str
    source_hash: str
    outputs_hash: str
    execution_count: int | None


@dataclass(frozen=True)
class _Config:
    server: str
    token: str | None
    actor_id: str
    actor_type: str
    timeout: float
    transport: httpx.BaseTransport | None = None


def connect(
    path: str,
    create: bool = False,
    *,
    server: str | None = None,
    token: str | None = None,
    actor_id: str = "python-sdk",
    actor_type: str = "human",
    timeout: float = 30.0,
    transport: httpx.BaseTransport | None = None,
) -> Notebook:
    """Connect to a notebook path on a Hypernote-enabled Jupyter server."""
    cfg = _Config(
        server=(server or os.environ.get("HYPERNOTE_SERVER", "http://127.0.0.1:8888")).rstrip("/"),
        token=token or os.environ.get("HYPERNOTE_TOKEN"),
        actor_id=actor_id,
        actor_type=actor_type,
        timeout=timeout,
        transport=transport,
    )
    notebook = Notebook(path=path, _config=cfg)
    notebook._ensure_exists(create=create)
    return notebook


class _SDKMixin:
    _config: _Config

    def _jupyter_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._config.token:
            headers["Authorization"] = f"token {self._config.token}"
        return headers

    def _hypernote_headers(self) -> dict[str, str]:
        return {
            **self._jupyter_headers(),
            "X-Hypernote-Actor-Id": self._config.actor_id,
            "X-Hypernote-Actor-Type": self._config.actor_type,
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        hypernote: bool = False,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        with httpx.Client(
            base_url=self._config.server,
            headers=self._hypernote_headers() if hypernote else self._jupyter_headers(),
            timeout=self._config.timeout,
            transport=self._config.transport,
        ) as client:
            response = client.request(method, path, json=json_body, params=params)
        return response


class _ControlPlane(_SDKMixin):
    """Internal control-plane helper for CLI/operator commands.

    This keeps low-level job and diagnostics transport logic in one place
    without expanding the public notebook-first SDK surface.
    """

    def __init__(self, config: _Config):
        self._config = config

    def get_job_payload(self, job_id: str) -> dict[str, Any]:
        response = self._request(
            "GET",
            f"/hypernote/api/jobs/{job_id}",
            hypernote=True,
        )
        _raise_response(response)
        return response.json()

    def get_job(self, job_id: str) -> Job:
        return _job_from_payload(self._config, self.get_job_payload(job_id))

    def list_jobs(
        self,
        *,
        notebook_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if notebook_id:
            params["notebook_id"] = notebook_id
        if status:
            params["status"] = status
        response = self._request(
            "GET",
            "/hypernote/api/jobs",
            hypernote=True,
            params=params or None,
        )
        _raise_response(response)
        return response.json()

    def get_notebook_document(
        self,
        notebook_id: str,
        *,
        content: bool = True,
    ) -> dict[str, Any]:
        quoted = urllib.parse.quote(notebook_id, safe="")
        response = self._request(
            "GET",
            f"/hypernote/api/notebooks/{quoted}/document",
            hypernote=True,
            params={"content": int(content)},
        )
        _raise_notebook_response(response, notebook_id)
        return response.json()

    def get_runtime_status(self, notebook_id: str) -> dict[str, Any]:
        quoted = urllib.parse.quote(notebook_id, safe="")
        response = self._request(
            "GET",
            f"/hypernote/api/notebooks/{quoted}/runtime",
            hypernote=True,
        )
        _raise_notebook_response(response, notebook_id)
        return response.json()

    def get_kernelspec(self, kernel_name: str) -> dict[str, Any]:
        quoted = urllib.parse.quote(kernel_name, safe="")
        response = self._request(
            "GET",
            f"/api/kernelspecs/{quoted}",
        )
        _raise_response(response)
        return response.json()

    def send_job_stdin(self, job_id: str, value: str) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"/hypernote/api/jobs/{job_id}/stdin",
            hypernote=True,
            json_body={"value": value},
        )
        try:
            _raise_response(response)
        except HypernoteError as exc:
            if response.status_code == 400:
                raise InputNotExpectedError(str(exc)) from exc
            raise
        return response.json()


class Notebook(_SDKMixin):
    """Main user-facing notebook handle."""

    def __init__(self, path: str, _config: _Config):
        self.path = path
        self._config = _config
        self.cells = CellCollection(self)
        self.runtime = Runtime(self)

    def _quote_path(self) -> str:
        return urllib.parse.quote(self.path, safe="")

    def _ensure_exists(self, create: bool) -> None:
        response = self._request(
            "GET",
            f"/hypernote/api/notebooks/{self._quote_path()}/document",
            hypernote=True,
            params={"content": 0},
        )
        if response.status_code == 404 and create:
            model = _new_notebook_model()
            created = self._request(
                "PUT",
                f"/hypernote/api/notebooks/{self._quote_path()}/document",
                hypernote=True,
                json_body=model,
            )
            _raise_notebook_response(created, self.path)
            return
        _raise_notebook_response(response, self.path)

    def _get_notebook_model(self, *, content: bool = True) -> dict[str, Any]:
        response = self._request(
            "GET",
            f"/hypernote/api/notebooks/{self._quote_path()}/document",
            hypernote=True,
            params={"content": int(content)},
        )
        _raise_notebook_response(response, self.path)
        return response.json()

    def _save_notebook_model(self, model: dict[str, Any]) -> dict[str, Any]:
        response = self._request(
            "PUT",
            f"/hypernote/api/notebooks/{self._quote_path()}/document",
            hypernote=True,
            json_body=model,
        )
        _raise_notebook_response(response, self.path)
        return response.json()

    def _cell_model(self, cell_id: str) -> dict[str, Any]:
        quoted_cell_id = urllib.parse.quote(cell_id, safe="")
        response = self._request(
            "GET",
            f"/hypernote/api/notebooks/{self._quote_path()}/cells/{quoted_cell_id}",
            hypernote=True,
        )
        if response.status_code == 404:
            raise CellNotFoundError(cell_id)
        _raise_response(response)
        return response.json()["cell"]

    def _cell_order(self) -> list[dict[str, Any]]:
        model = self._get_notebook_model(content=True)
        return list(model["content"].get("cells", []))

    def _kernelspec_name(self) -> str:
        model = self._get_notebook_model(content=True)
        metadata = model["content"].get("metadata", {})
        return metadata.get("kernelspec", {}).get("name", "python3")

    def _run_cells(self, cell_ids: list[str]) -> Job:
        if not cell_ids:
            raise HypernoteError("No code cells to run")
        response = self._request(
            "POST",
            f"/hypernote/api/notebooks/{self._quote_path()}/execute",
            hypernote=True,
            json_body={"cell_ids": cell_ids},
        )
        _raise_response(response)
        payload = response.json()
        return Job(
            notebook=self,
            id=payload["job_id"],
            status=JobStatus(payload["status"]),
            cell_ids=tuple(cell_ids),
            notebook_path=self.path,
        )

    def run(self, *cell_ids: str) -> Job:
        normalized: list[str] = []
        for cell_id in cell_ids:
            if isinstance(cell_id, (list, tuple)):
                normalized.extend(str(item) for item in cell_id)
            else:
                normalized.append(str(cell_id))
        return self._run_cells(normalized)

    def run_all(self) -> Job:
        cells = self._cell_order()
        code_ids = [cell["id"] for cell in cells if cell.get("cell_type") == CellType.CODE.value]
        return self._run_cells(code_ids)

    def interrupt(self) -> None:
        response = self._request(
            "POST",
            f"/hypernote/api/notebooks/{self._quote_path()}/interrupt",
            hypernote=True,
            json_body={},
        )
        _raise_response(response)

    def restart(self) -> Runtime:
        self.runtime.stop()
        return self.runtime.ensure()

    def snapshot(self) -> Snapshot:
        cells = self._cell_order()
        token = _encode_snapshot_token(cells)
        return Snapshot(token=token, timestamp=time.time(), cell_count=len(cells))

    def status(self, *, full: bool = False) -> NotebookStatus:
        cells = self._cell_order()
        current = Snapshot(
            token=_encode_snapshot_token(cells),
            timestamp=time.time(),
            cell_count=len(cells),
        )
        runtime_status = self.runtime.status
        cell_statuses = tuple(
            _build_cell_status(
                cell,
                full=full,
                changed=False,
                change_kinds=(),
            )
            for cell in cells
        )
        return NotebookStatus(
            notebook_path=self.path,
            baseline=None,
            current=current,
            runtime=runtime_status,
            cells=cell_statuses,
            summary=_build_summary(self.path, runtime_status, cells, diff=False),
        )

    def diff(self, *, snapshot: Snapshot, full: bool = False) -> NotebookStatus:
        cells = self._cell_order()
        current = Snapshot(
            token=_encode_snapshot_token(cells),
            timestamp=time.time(),
            cell_count=len(cells),
        )
        baseline_data = _decode_snapshot_token(snapshot.token)
        current_data = _snapshot_cells(cells)

        baseline_by_id = {cell.id: cell for cell in baseline_data}
        current_by_id = {cell.id: cell for cell in current_data}
        changed: list[CellStatus] = []

        for cell in cells:
            cell_id = cell.get("id")
            current_entry = current_by_id[cell_id]
            baseline_entry = baseline_by_id.get(cell_id)
            change_kinds: list[ChangeKind] = []
            if baseline_entry is None:
                change_kinds.append(ChangeKind.ADDED)
            else:
                if baseline_entry.order != current_entry.order:
                    change_kinds.append(ChangeKind.MOVED)
                if baseline_entry.source_hash != current_entry.source_hash:
                    change_kinds.append(ChangeKind.SOURCE_EDITED)
                if baseline_entry.outputs_hash != current_entry.outputs_hash:
                    change_kinds.append(ChangeKind.OUTPUT_CHANGED)
                if baseline_entry.execution_count != current_entry.execution_count:
                    change_kinds.append(ChangeKind.EXECUTION_COUNT)
            if change_kinds:
                changed.append(
                    _build_cell_status(
                        cell,
                        full=full,
                        changed=True,
                        change_kinds=tuple(change_kinds),
                    )
                )

        for removed_id, removed in baseline_by_id.items():
            if removed_id not in current_by_id:
                changed.append(
                    CellStatus(
                        id=removed_id,
                        type=CellType(removed.type),
                        changed=True,
                        change_kinds=(ChangeKind.DELETED,),
                        source=None,
                        outputs=None,
                        execution_count=removed.execution_count,
                    )
                )

        runtime_status = self.runtime.status
        return NotebookStatus(
            notebook_path=self.path,
            baseline=snapshot,
            current=current,
            runtime=runtime_status,
            cells=tuple(changed),
            summary=_build_summary(
                self.path,
                runtime_status,
                cells,
                diff=True,
                changed_count=len(changed),
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return self.status(full=True).to_dict()


class CellCollection:
    """Lookup, iteration, and insertion surface for notebook cells."""

    def __init__(self, notebook: Notebook):
        self._notebook = notebook

    def __getitem__(self, cell_id: str) -> CellHandle:
        _ = self._notebook._cell_model(cell_id)
        return CellHandle(self._notebook, cell_id)

    def __iter__(self) -> Iterator[CellHandle]:
        for cell in self._notebook._cell_order():
            yield CellHandle(self._notebook, cell["id"])

    def __len__(self) -> int:
        return len(self._notebook._cell_order())

    def __contains__(self, cell_id: str) -> bool:
        try:
            self._notebook._cell_model(cell_id)
            return True
        except CellNotFoundError:
            return False

    def insert_code(
        self,
        source: str,
        *,
        id: str | None = None,
        before: str | None = None,
        after: str | None = None,
    ) -> CellHandle:
        return self._insert(CellType.CODE, source, id=id, before=before, after=after)

    def insert_markdown(
        self,
        source: str,
        *,
        id: str | None = None,
        before: str | None = None,
        after: str | None = None,
    ) -> CellHandle:
        return self._insert(CellType.MARKDOWN, source, id=id, before=before, after=after)

    def _insert(
        self,
        cell_type: CellType,
        source: str,
        *,
        id: str | None,
        before: str | None,
        after: str | None,
    ) -> CellHandle:
        _validate_position(before=before, after=after)
        cell_id = id or _generated_cell_id()
        response = self._notebook._request(
            "POST",
            f"/hypernote/api/notebooks/{self._notebook._quote_path()}/cells",
            hypernote=True,
            json_body={
                "id": cell_id,
                "cell_type": cell_type.value,
                "source": source,
                "before": before,
                "after": after,
            },
        )
        _raise_response(response)
        return CellHandle(self._notebook, cell_id)


class CellHandle:
    """Live notebook-bound handle for a specific cell."""

    def __init__(self, notebook: Notebook, cell_id: str):
        self._notebook = notebook
        self.id = cell_id

    @property
    def _cell(self) -> dict[str, Any]:
        return self._notebook._cell_model(self.id)

    @property
    def type(self) -> CellType:
        return CellType(self._cell.get("cell_type", CellType.CODE.value))

    @property
    def source(self) -> str:
        return _cell_source(self._cell)

    @property
    def outputs(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._cell.get("outputs", []))

    @property
    def execution_count(self) -> int | None:
        return self._cell.get("execution_count")

    def replace(self, source: str) -> CellHandle:
        quoted_cell_id = urllib.parse.quote(self.id, safe="")
        response = self._notebook._request(
            "PATCH",
            f"/hypernote/api/notebooks/{self._notebook._quote_path()}/cells/{quoted_cell_id}",
            hypernote=True,
            json_body={"source": source},
        )
        _raise_response(response)
        return self

    def delete(self) -> None:
        quoted_cell_id = urllib.parse.quote(self.id, safe="")
        response = self._notebook._request(
            "DELETE",
            f"/hypernote/api/notebooks/{self._notebook._quote_path()}/cells/{quoted_cell_id}",
            hypernote=True,
        )
        _raise_response(response)

    def move(self, *, before: str | None = None, after: str | None = None) -> None:
        _validate_position(before=before, after=after)
        quoted_cell_id = urllib.parse.quote(self.id, safe="")
        response = self._notebook._request(
            "POST",
            f"/hypernote/api/notebooks/{self._notebook._quote_path()}/cells/{quoted_cell_id}/move",
            hypernote=True,
            json_body={"before": before, "after": after},
        )
        _raise_response(response)

    def clear_outputs(self) -> CellHandle:
        quoted_cell_id = urllib.parse.quote(self.id, safe="")
        response = self._notebook._request(
            "POST",
            (
                f"/hypernote/api/notebooks/{self._notebook._quote_path()}/cells/"
                f"{quoted_cell_id}/clear-outputs"
            ),
            hypernote=True,
            json_body={},
        )
        _raise_response(response)
        return self

    def run(self) -> Job:
        if self.type != CellType.CODE:
            raise HypernoteError(f"Cell {self.id} is {self.type.value}, not code")
        return self._notebook._run_cells([self.id])

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "source": self.source,
            "outputs": list(self.outputs),
            "execution_count": self.execution_count,
        }


class Runtime:
    """Live execution state for a notebook."""

    def __init__(self, notebook: Notebook):
        self._notebook = notebook

    def _payload(self) -> dict[str, Any]:
        response = self._notebook._request(
            "GET",
            f"/hypernote/api/notebooks/{self._notebook._quote_path()}/runtime",
            hypernote=True,
        )
        _raise_response(response)
        return response.json()

    @property
    def status(self) -> RuntimeStatus:
        return RuntimeStatus(self._payload()["state"])

    @property
    def recoverable(self) -> bool:
        return bool(self._payload().get("recoverable", False))

    @property
    def session_id(self) -> str | None:
        return self._payload().get("session_id")

    @property
    def kernel_id(self) -> str | None:
        return self._payload().get("kernel_id")

    @property
    def kernel_name(self) -> str | None:
        return self._payload().get("kernel_name")

    def ensure(self) -> Runtime:
        response = self._notebook._request(
            "POST",
            f"/hypernote/api/notebooks/{self._notebook._quote_path()}/runtime/open",
            hypernote=True,
            json_body={
                "client_id": self._notebook._config.actor_id,
                "kernel_name": self._notebook._kernelspec_name(),
            },
        )
        if response.status_code == 400:
            raise RuntimeUnavailableError(response.text or "Runtime unavailable")
        _raise_response(response)
        return self

    def stop(self) -> Runtime:
        response = self._notebook._request(
            "POST",
            f"/hypernote/api/notebooks/{self._notebook._quote_path()}/runtime/stop",
            hypernote=True,
            json_body={},
        )
        if response.status_code == 400:
            raise RuntimeUnavailableError(response.text or "Runtime unavailable")
        _raise_response(response)
        return self

    def to_dict(self) -> dict[str, Any]:
        payload = self._payload()
        payload["status"] = payload.pop("state")
        return payload


@dataclass
class Job:
    notebook: Notebook
    id: str
    status: JobStatus
    cell_ids: tuple[str, ...]
    notebook_path: str

    def refresh(self) -> Job:
        response = self.notebook._request(
            "GET",
            f"/hypernote/api/jobs/{self.id}",
            hypernote=True,
        )
        _raise_response(response)
        payload = response.json()
        self.status = JobStatus(payload["status"])
        if payload.get("target_cells"):
            self.cell_ids = tuple(json.loads(payload["target_cells"]))
        return self

    def wait(self, timeout: float | None = None) -> Job:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            self.refresh()
            if self.status in {
                JobStatus.SUCCEEDED,
                JobStatus.FAILED,
                JobStatus.INTERRUPTED,
                JobStatus.AWAITING_INPUT,
            }:
                return self
            if deadline is not None and time.monotonic() >= deadline:
                raise ExecutionTimeoutError(_job_timeout_message(self))
            time.sleep(0.25)

    def send_stdin(self, value: str) -> None:
        response = self.notebook._request(
            "POST",
            f"/hypernote/api/jobs/{self.id}/stdin",
            hypernote=True,
            json_body={"value": value},
        )
        try:
            _raise_response(response)
        except HypernoteError as exc:
            if response.status_code == 400:
                raise InputNotExpectedError(str(exc)) from exc
            raise

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status.value,
            "cell_ids": list(self.cell_ids),
            "notebook_path": self.notebook_path,
        }


def _new_notebook_model() -> dict[str, Any]:
    return {
        "type": "notebook",
        "format": "json",
        "content": {
            "cells": [],
            "metadata": {
                "kernelspec": {"display_name": "Python 3", "name": "python3"},
                "language_info": {"name": "python"},
            },
            "nbformat": 4,
            "nbformat_minor": 5,
        },
    }


def _generated_cell_id() -> str:
    return hashlib.sha1(f"{time.time_ns()}".encode()).hexdigest()[:12]


def _control_plane(config: _Config) -> _ControlPlane:
    return _ControlPlane(config)


def _job_from_payload(config: _Config, payload: dict[str, Any]) -> Job:
    target_cells: tuple[str, ...] = ()
    if payload.get("target_cells"):
        target_cells = tuple(json.loads(payload["target_cells"]))
    notebook_path = payload["notebook_id"]
    notebook = Notebook(path=notebook_path, _config=config)
    return Job(
        notebook=notebook,
        id=payload["job_id"],
        status=JobStatus(payload["status"]),
        cell_ids=target_cells,
        notebook_path=notebook_path,
    )


def _validate_position(*, before: str | None, after: str | None) -> None:
    if before is not None and after is not None:
        raise HypernoteError("Specify only one of before= or after=")


def _resolve_insert_index(
    cells: list[dict[str, Any]],
    *,
    before: str | None,
    after: str | None,
) -> int:
    if before is None and after is None:
        return len(cells)
    if before is not None:
        return _find_cell_index(cells, before)
    return _find_cell_index(cells, after) + 1


def _find_cell_index(cells: list[dict[str, Any]], cell_id: str) -> int:
    for index, cell in enumerate(cells):
        if cell.get("id") == cell_id:
            return index
    raise CellNotFoundError(cell_id)


def _assign_position_keys(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for index, cell in enumerate(cells):
        metadata = dict(cell.get("metadata") or {})
        hypernote_meta = dict(metadata.get("hypernote") or {})
        hypernote_meta["position_key"] = f"{index:09d}"
        metadata["hypernote"] = hypernote_meta
        cell["metadata"] = metadata
    return cells


def _cell_source(cell: dict[str, Any]) -> str:
    source = cell.get("source", "")
    if isinstance(source, list):
        return "".join(source)
    return str(source)


def _snapshot_cells(cells: list[dict[str, Any]]) -> tuple[_SnapshotCell, ...]:
    entries = []
    for index, cell in enumerate(cells):
        metadata = cell.get("metadata") or {}
        hypernote_meta = metadata.get("hypernote") or {}
        order = hypernote_meta.get("position_key", f"{index:09d}")
        entries.append(
            _SnapshotCell(
                id=cell["id"],
                type=cell.get("cell_type", CellType.CODE.value),
                order=order,
                source_hash=_sha256(_cell_source(cell)),
                outputs_hash=_sha256(
                    json.dumps(
                        cell.get("outputs", []),
                        sort_keys=True,
                        default=str,
                    )
                ),
                execution_count=cell.get("execution_count"),
            )
        )
    return tuple(entries)


def _encode_snapshot_token(cells: list[dict[str, Any]]) -> str:
    payload = {"cells": [asdict(entry) for entry in _snapshot_cells(cells)]}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_snapshot_token(token: str) -> tuple[_SnapshotCell, ...]:
    try:
        raw = base64.urlsafe_b64decode(token.encode())
        payload = json.loads(raw.decode())
        return tuple(_SnapshotCell(**entry) for entry in payload["cells"])
    except Exception as exc:  # pragma: no cover - defensive
        raise HypernoteError("Invalid snapshot token") from exc


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _build_cell_status(
    cell: dict[str, Any],
    *,
    full: bool,
    changed: bool,
    change_kinds: tuple[ChangeKind, ...],
) -> CellStatus:
    source = _cell_source(cell)
    outputs = cell.get("outputs", [])
    return CellStatus(
        id=cell["id"],
        type=CellType(cell.get("cell_type", CellType.CODE.value)),
        changed=changed,
        change_kinds=change_kinds,
        source=source if full else _truncate(source, SUMMARY_SOURCE_CHARS),
        outputs=tuple(outputs) if full else tuple(_summarize_output(output) for output in outputs),
        execution_count=cell.get("execution_count"),
    )


def _summarize_output(output: dict[str, Any]) -> dict[str, Any]:
    output_type = output.get("output_type", "unknown")
    if output_type == "stream":
        return {
            "output_type": output_type,
            "name": output.get("name"),
            "text": _truncate(str(output.get("text", "")), SUMMARY_OUTPUT_TEXT_CHARS),
        }
    if output_type in {"display_data", "execute_result"}:
        data = output.get("data", {})
        return {
            "output_type": output_type,
            "data_keys": sorted(data.keys()),
            "text": _truncate(str(data.get("text/plain", "")), SUMMARY_OUTPUT_TEXT_CHARS),
        }
    if output_type == "error":
        return {
            "output_type": output_type,
            "ename": output.get("ename"),
            "evalue": output.get("evalue"),
        }
    return {"output_type": output_type}


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _build_summary(
    notebook_path: str,
    runtime_status: RuntimeStatus,
    cells: list[dict[str, Any]],
    *,
    diff: bool,
    changed_count: int = 0,
) -> str:
    code_cells = sum(1 for cell in cells if cell.get("cell_type") == CellType.CODE.value)
    markdown_cells = sum(1 for cell in cells if cell.get("cell_type") == CellType.MARKDOWN.value)
    executed = sum(1 for cell in cells if cell.get("execution_count") is not None)
    if diff:
        return (
            f"{os.path.basename(notebook_path)} · {changed_count} changed cells · "
            f"runtime {runtime_status.value}"
        )
    return (
        f"{os.path.basename(notebook_path)} · {len(cells)} cells "
        f"({code_cells} code, {markdown_cells} markdown) · "
        f"{executed} executed · runtime {runtime_status.value}"
    )


def _raise_notebook_response(response: httpx.Response, notebook_path: str) -> None:
    if response.is_success:
        return
    if response.status_code == 404:
        raise NotebookNotFoundError(notebook_path)
    _raise_response(response)


def _raise_response(response: httpx.Response) -> None:
    if response.is_success:
        return
    if response.status_code == 404:
        raise HypernoteError("Resource not found")
    if response.status_code == 400:
        raise HypernoteError(response.text or "Bad request")
    raise HypernoteError(f"{response.status_code}: {response.text}")


def _job_timeout_message(job: Job) -> str:
    return (
        f"Timed out waiting for job {job.id} "
        f"(last status: {job.status.value}). "
        f"Check `hypernote job get {job.id}` or "
        f"`hypernote cat {job.notebook_path} --no-outputs` for current state."
    )
