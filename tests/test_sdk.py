"""Tests for the synchronous Hypernote SDK."""

from __future__ import annotations

import json
import urllib.parse

import httpx

import hypernote
from hypernote import (
    CellNotFoundError,
    CellType,
    ChangeKind,
    HypernoteError,
    InputNotExpectedError,
    JobStatus,
    RuntimeStatus,
)


def _make_transport():
    state = {
        "notebooks": {},
        "jobs": {},
        "job_counter": 0,
        "runtime": {
            "state": "stopped",
            "room_id": None,
            "session_id": None,
            "kernel_id": None,
            "kernel_name": None,
            "attached_clients": [],
            "active_jobs": [],
            "last_activity": None,
            "recoverable": False,
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method

        if path.startswith("/hypernote/api/notebooks/") and path.endswith("/document"):
            notebook_path = urllib.parse.unquote(
                path.removeprefix("/hypernote/api/notebooks/").removesuffix("/document")
            )
            if method == "GET":
                if notebook_path not in state["notebooks"]:
                    return httpx.Response(404, request=request)
                content = request.url.params.get("content", "1")
                notebook = state["notebooks"][notebook_path]
                body = {"path": notebook_path, "type": "notebook"}
                if content != "0":
                    body["content"] = notebook
                return httpx.Response(200, request=request, json=body)
            if method == "PUT":
                payload = json.loads(request.content.decode())
                state["notebooks"][notebook_path] = payload["content"]
                return httpx.Response(
                    200,
                    request=request,
                    json={"path": notebook_path, "type": "notebook", "content": payload["content"]},
                )

        if (
            path.startswith("/hypernote/api/notebooks/")
            and path.endswith("/cells")
            and method == "POST"
        ):
            notebook_path = urllib.parse.unquote(
                path.removeprefix("/hypernote/api/notebooks/").removesuffix("/cells")
            )
            payload = json.loads(request.content.decode())
            notebook = state["notebooks"][notebook_path]
            cells = notebook["cells"]
            index = len(cells)
            if payload.get("before"):
                index = next(
                    i for i, cell in enumerate(cells) if cell["id"] == payload["before"]
                )
            elif payload.get("after"):
                index = (
                    next(i for i, cell in enumerate(cells) if cell["id"] == payload["after"])
                    + 1
                )
            cells.insert(
                index,
                {
                    "id": payload["id"],
                    "cell_type": payload["cell_type"],
                    "execution_count": None,
                    "metadata": {},
                    "outputs": [],
                    "source": payload["source"],
                },
            )
            return httpx.Response(201, request=request, json={"cell": cells[index]})

        if "/cells/" in path:
            notebook_path = urllib.parse.unquote(path.split("/notebooks/")[1].split("/cells/")[0])
            cell_part = path.split("/cells/")[1]
            cell_id = urllib.parse.unquote(cell_part.split("/")[0])
            notebook = state["notebooks"][notebook_path]
            cells = notebook["cells"]
            cell = next((cell for cell in cells if cell["id"] == cell_id), None)
            if cell is None:
                return httpx.Response(404, request=request)
            if method == "GET" and "/" not in cell_part:
                return httpx.Response(200, request=request, json={"cell": cell})
            if method == "PATCH":
                payload = json.loads(request.content.decode())
                cell["source"] = payload["source"]
                return httpx.Response(200, request=request, json={"cell": cell})
            if method == "DELETE":
                cells[:] = [existing for existing in cells if existing["id"] != cell_id]
                return httpx.Response(200, request=request, json={"deleted": True})
            if method == "POST" and cell_part.endswith("/move"):
                payload = json.loads(request.content.decode())
                moving = cell
                cells[:] = [existing for existing in cells if existing["id"] != cell_id]
                index = len(cells)
                if payload.get("before"):
                    index = next(
                        i
                        for i, existing in enumerate(cells)
                        if existing["id"] == payload["before"]
                    )
                elif payload.get("after"):
                    index = (
                        next(
                            i
                            for i, existing in enumerate(cells)
                            if existing["id"] == payload["after"]
                        )
                        + 1
                    )
                cells.insert(index, moving)
                return httpx.Response(200, request=request, json={"cell": moving})
            if method == "POST" and cell_part.endswith("/clear-outputs"):
                cell["outputs"] = []
                cell["execution_count"] = None
                return httpx.Response(200, request=request, json={"cell": cell})

        if path.endswith("/runtime") and method == "GET":
            return httpx.Response(200, request=request, json=state["runtime"])

        if path.endswith("/runtime/open") and method == "POST":
            payload = json.loads(request.content.decode())
            state["runtime"] = {
                "state": "live-attached",
                "room_id": "room-1",
                "session_id": "session-1",
                "kernel_id": "kernel-1",
                "kernel_name": payload.get("kernel_name", "python3"),
                "attached_clients": [payload.get("client_id", "python-sdk")],
                "active_jobs": [],
                "last_activity": 1.0,
                "recoverable": False,
            }
            return httpx.Response(200, request=request, json=state["runtime"])

        if path.endswith("/runtime/stop") and method == "POST":
            state["runtime"]["state"] = "stopped"
            state["runtime"]["kernel_id"] = None
            state["runtime"]["session_id"] = None
            return httpx.Response(
                200,
                request=request,
                json={"room_id": "room-1", "state": "stopped"},
            )

        if path.endswith("/interrupt") and method == "POST":
            return httpx.Response(200, request=request, json={"interrupted": True})

        if path.endswith("/execute") and method == "POST":
            payload = json.loads(request.content.decode())
            notebook_path = urllib.parse.unquote(path.split("/notebooks/")[1].split("/execute")[0])
            state["job_counter"] += 1
            job_id = f"job-{state['job_counter']}"
            state["jobs"][job_id] = {
                "job_id": job_id,
                "status": "succeeded",
                "target_cells": json.dumps(payload["cell_ids"]),
            }
            notebook = state["notebooks"][notebook_path]
            for cell in notebook["cells"]:
                if cell["id"] in payload["cell_ids"]:
                    cell["execution_count"] = 1
                    cell["outputs"] = [{"output_type": "stream", "name": "stdout", "text": "42\n"}]
            return httpx.Response(
                202,
                request=request,
                json={"job_id": job_id, "status": "succeeded", "request_uids": ["req-1"]},
            )

        if "/jobs/" in path and path.endswith("/stdin") and method == "POST":
            return httpx.Response(200, request=request, json={"sent": True})

        if path.startswith("/hypernote/api/jobs/") and method == "GET":
            job_id = path.rsplit("/", 1)[-1]
            return httpx.Response(200, request=request, json=state["jobs"][job_id])

        raise AssertionError(f"Unhandled request: {method} {path}")

    return state, httpx.MockTransport(handler)


def test_connect_create_and_cell_editing():
    _, transport = _make_transport()
    nb = hypernote.connect("tmp/sdk.ipynb", create=True, server="http://test", transport=transport)

    cell = nb.cells.insert_code("print(42)", id="hello-cell")
    assert cell.id == "hello-cell"
    assert cell.type == CellType.CODE

    markdown = nb.cells.insert_markdown("# Title", id="title", before="hello-cell")
    assert markdown.id == "title"
    assert [cell.id for cell in nb.cells] == ["title", "hello-cell"]

    cell.replace("print(43)")
    assert nb.cells["hello-cell"].source == "print(43)"

    nb.cells["title"].move(after="hello-cell")
    assert [cell.id for cell in nb.cells] == ["hello-cell", "title"]

    nb.cells["title"].delete()
    assert len(nb.cells) == 1
    assert "title" not in nb.cells


def test_cell_run_runtime_and_restart():
    _, transport = _make_transport()
    nb = hypernote.connect("tmp/sdk.ipynb", create=True, server="http://test", transport=transport)
    nb.cells.insert_code("print(42)", id="hello-cell")

    runtime = nb.runtime.ensure()
    assert runtime.status == RuntimeStatus.LIVE_ATTACHED
    assert runtime.kernel_name == "python3"

    job = nb.cells["hello-cell"].run()
    assert job.status == JobStatus.SUCCEEDED
    assert job.wait().status == JobStatus.SUCCEEDED
    assert nb.cells["hello-cell"].execution_count == 1

    restarted = nb.restart()
    assert restarted.status == RuntimeStatus.LIVE_ATTACHED


def test_status_and_diff():
    _, transport = _make_transport()
    nb = hypernote.connect("tmp/sdk.ipynb", create=True, server="http://test", transport=transport)
    nb.cells.insert_code("print(42)", id="hello-cell")
    snap = nb.snapshot()

    status = nb.status()
    assert status.runtime == RuntimeStatus.STOPPED
    assert len(status.cells) == 1
    assert status.cells[0].source == "print(42)"

    nb.cells["hello-cell"].replace("print(43)")
    diff = nb.diff(snapshot=snap)
    assert len(diff.cells) == 1
    assert diff.cells[0].changed is True
    assert diff.cells[0].change_kinds


def test_diff_reports_added_source_edit_and_execution_changes():
    _, transport = _make_transport()
    nb = hypernote.connect("tmp/sdk.ipynb", create=True, server="http://test", transport=transport)

    baseline = nb.snapshot()
    inserted = nb.cells.insert_code("value = 1\nprint(value)", id="delta-cell")
    added = nb.diff(snapshot=baseline, full=True)
    assert len(added.cells) == 1
    assert added.cells[0].id == "delta-cell"
    assert added.cells[0].change_kinds == (ChangeKind.ADDED,)

    edit_baseline = nb.snapshot()
    inserted.replace("value = 2\nprint(value)")
    edited = nb.diff(snapshot=edit_baseline, full=True)
    assert len(edited.cells) == 1
    assert edited.cells[0].id == "delta-cell"
    assert edited.cells[0].change_kinds == (ChangeKind.SOURCE_EDITED,)

    run_baseline = nb.snapshot()
    job = inserted.run()
    assert job.wait().status == JobStatus.SUCCEEDED
    executed = nb.diff(snapshot=run_baseline, full=True)
    assert len(executed.cells) == 1
    assert executed.cells[0].id == "delta-cell"
    assert set(executed.cells[0].change_kinds) == {
        ChangeKind.OUTPUT_CHANGED,
        ChangeKind.EXECUTION_COUNT,
    }


def test_diff_reports_deleted_cells():
    _, transport = _make_transport()
    nb = hypernote.connect("tmp/sdk.ipynb", create=True, server="http://test", transport=transport)
    doomed = nb.cells.insert_code("print('bye')", id="doomed-cell")
    baseline = nb.snapshot()

    doomed.delete()

    diff = nb.diff(snapshot=baseline, full=True)
    assert len(diff.cells) == 1
    assert diff.cells[0].id == "doomed-cell"
    assert diff.cells[0].change_kinds == (ChangeKind.DELETED,)


def test_job_send_stdin_maps_bad_request_to_input_not_expected():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/document"):
            return httpx.Response(
                200,
                request=request,
                json={"path": "tmp/sdk.ipynb", "type": "notebook", "content": {"cells": []}},
            )
        if path.endswith("/execute"):
            return httpx.Response(
                202,
                request=request,
                json={"job_id": "job-1", "status": "awaiting_input", "request_uids": ["req-1"]},
            )
        if path.endswith("/stdin"):
            return httpx.Response(400, request=request, text="stdin not expected")
        if path.endswith("/jobs/job-1"):
            return httpx.Response(
                200,
                request=request,
                json={"job_id": "job-1", "status": "awaiting_input", "target_cells": '["cell-a"]'},
            )
        raise AssertionError(f"Unhandled request: {request.method} {path}")

    transport = httpx.MockTransport(handler)
    nb = hypernote.connect("tmp/sdk.ipynb", create=False, server="http://test", transport=transport)
    job = hypernote.Job(
        notebook=nb,
        id="job-1",
        status=JobStatus.AWAITING_INPUT,
        cell_ids=("cell-a",),
        notebook_path=nb.path,
    )

    try:
        job.send_stdin("hello")
    except InputNotExpectedError as exc:
        assert "stdin not expected" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected InputNotExpectedError")


def test_invalid_snapshot_token_raises_hypernote_error():
    _, transport = _make_transport()
    nb = hypernote.connect("tmp/sdk.ipynb", create=True, server="http://test", transport=transport)

    try:
        nb.diff(snapshot=hypernote.Snapshot(token="not-base64", timestamp=0.0, cell_count=0))
    except HypernoteError as exc:
        assert "Invalid snapshot token" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected HypernoteError")


def test_missing_cell_raises_key_error():
    _, transport = _make_transport()
    nb = hypernote.connect("tmp/sdk.ipynb", create=True, server="http://test", transport=transport)
    try:
        nb.cells["missing"]
    except CellNotFoundError:
        pass
    else:  # pragma: no cover
        raise AssertionError("Expected CellNotFoundError")
