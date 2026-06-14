"""Tests for the synchronous Hypernote SDK."""

from __future__ import annotations

import base64
import json
import urllib.parse

import httpx
import pytest

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
            # Mirror the server: no runtime room for the notebook -> 404.
            if state["runtime"]["state"] == "stopped":
                return httpx.Response(
                    404,
                    request=request,
                    json={"message": "No runtime for notebook"},
                )
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


def test_restart_with_no_live_runtime_brings_up_kernel():
    # Regression: on a notebook whose kernel was never opened, the server
    # returns 404 for POST .../runtime/stop. restart() must treat that as a
    # no-op stop and still ensure a fresh runtime instead of erroring with
    # "Resource not found".
    _, transport = _make_transport()
    nb = hypernote.connect("tmp/sdk.ipynb", create=True, server="http://test", transport=transport)
    nb.cells.insert_code("print(42)", id="hello-cell")

    assert nb.runtime.status == RuntimeStatus.STOPPED

    restarted = nb.restart()
    assert restarted.status == RuntimeStatus.LIVE_ATTACHED


def test_stop_is_idempotent_when_no_runtime():
    # Stopping a runtime that was never opened is a no-op, not an error.
    _, transport = _make_transport()
    nb = hypernote.connect("tmp/sdk.ipynb", create=True, server="http://test", transport=transport)

    stopped = nb.runtime.stop()
    assert stopped.status == RuntimeStatus.STOPPED


def test_job_wait_timeout_surfaces_recovery_hint():
    state, transport = _make_transport()
    nb = hypernote.connect("tmp/sdk.ipynb", create=True, server="http://test", transport=transport)
    nb.cells.insert_code("print(42)", id="hello-cell")
    state["jobs"]["job-timeout"] = {
        "job_id": "job-timeout",
        "status": "running",
        "target_cells": json.dumps(["hello-cell"]),
    }

    job = hypernote.Job(
        notebook=nb,
        id="job-timeout",
        status=JobStatus.RUNNING,
        cell_ids=("hello-cell",),
        notebook_path=nb.path,
    )

    with pytest.raises(hypernote.ExecutionTimeoutError, match="job-timeout") as excinfo:
        job.wait(timeout=0)

    message = str(excinfo.value)
    assert "last status: running" in message
    assert "hypernote job get job-timeout" in message
    assert "hypernote cat tmp/sdk.ipynb --no-outputs" in message


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


def test_status_compact_dict_and_cell_helpers():
    _, transport = _make_transport()
    nb = hypernote.connect(
        "tmp/sdk-compact-helpers.ipynb",
        create=True,
        server="http://test",
        transport=transport,
    )
    cell = nb.cells.insert_code("print('hello world')", id="hello-cell")

    job = cell.run()
    assert job.wait().status == JobStatus.SUCCEEDED

    status = nb.status(full=True)
    compact = status.compact_dict(include_outputs=True, max_output_chars=20)

    assert compact["cells_total"] == 1
    assert compact["summary"]["cell_count"] == 1
    assert compact["runtime_state"] == status.runtime.value
    assert len(compact["cells"]) == 1

    cell_payload = compact["cells"][0]
    assert cell_payload["id"] == "hello-cell"
    assert cell_payload["output_count"] == 1
    assert cell_payload["outputs"][0]["output_type"] == "stream"

    output_payload = status.cell("hello-cell").output_payload(max_chars=5, tail=True)
    assert output_payload["cell_id"] == "hello-cell"
    assert output_payload["output_count"] == 1
    assert output_payload["tail_output"]


def test_stream_output_lists_are_normalized_for_previews():
    state, transport = _make_transport()
    nb = hypernote.connect(
        "tmp/sdk-list-stream.ipynb",
        create=True,
        server="http://test",
        transport=transport,
    )
    cell = nb.cells.insert_code("print('hello')", id="list-stream-cell")
    stored_cell = state["notebooks"]["tmp/sdk-list-stream.ipynb"]["cells"][0]
    stored_cell["outputs"] = [
        {"output_type": "stream", "name": "stdout", "text": ["hello", " world\n"]}
    ]

    status = nb.status(full=True)
    preview = status.cell(cell.id).output_preview(full_output=True)
    payload = status.cell(cell.id).output_payload(full_output=True)

    assert preview["text"] == "hello world"
    assert payload["outputs"][0]["text"] == "hello world\n"


def test_output_previews_strip_ansi_escape_sequences():
    state, transport = _make_transport()
    nb = hypernote.connect(
        "tmp/sdk-ansi-output.ipynb",
        create=True,
        server="http://test",
        transport=transport,
    )
    cell = nb.cells.insert_code("1 / 0", id="ansi-error-cell")
    stored_cell = state["notebooks"]["tmp/sdk-ansi-output.ipynb"]["cells"][0]
    stored_cell["outputs"] = [
        {
            "output_type": "error",
            "ename": "ZeroDivisionError",
            "evalue": "division by zero",
            "traceback": ["\x1b[31mZeroDivisionError\x1b[0m: division by zero"],
        }
    ]

    status = nb.status(full=True)
    preview = status.cell(cell.id).output_preview(full_output=True)

    assert preview["text"] == "ZeroDivisionError: division by zero"


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


PNG_SOURCE_BYTES = b"\x89PNG\r\n\x1a\nfake-image-bytes"
PNG_BASE64 = base64.b64encode(PNG_SOURCE_BYTES).decode()
JPEG_SOURCE_BYTES = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
JPEG_BASE64 = base64.b64encode(JPEG_SOURCE_BYTES).decode()
SVG_TEXT = '<svg xmlns="http://www.w3.org/2000/svg"><rect width="4" height="4"/></svg>'


def _connect_with_rich_outputs(path: str):
    state, transport = _make_transport()
    nb = hypernote.connect(path, create=True, server="http://test", transport=transport)
    cell = nb.cells.insert_code("plot()", id="plot-cell")
    stored_cell = state["notebooks"][path]["cells"][0]
    stored_cell["outputs"] = [
        {
            "output_type": "display_data",
            "data": {
                "image/png": PNG_BASE64,
                "text/html": ["<div>", "figure</div>"],
                "text/plain": "<Figure size 640x480>",
            },
            "metadata": {"image/png": {"width": 640}},
        },
        {"output_type": "stream", "name": "stdout", "text": ["saved", " figure\n"]},
    ]
    stored_cell["execution_count"] = 1
    return nb, cell


def test_output_mime_bundles_return_raw_data_intact():
    nb, cell = _connect_with_rich_outputs("tmp/sdk-mime-intact.ipynb")

    status = nb.status(full=True)
    bundles = status.cell(cell.id).output_mime_bundles()

    assert bundles[0] == {
        "output_type": "display_data",
        "data": {
            "image/png": PNG_BASE64,
            "text/html": "<div>figure</div>",
            "text/plain": "<Figure size 640x480>",
        },
        "metadata": {"image/png": {"width": 640}},
    }
    assert bundles[1]["output_type"] == "stream"
    assert bundles[1]["data"]["text/plain"] == "saved figure\n"
    assert bundles[1]["metadata"]["stream_name"] == "stdout"


def test_output_mime_bundles_truncate_large_payloads_when_limited():
    nb, cell = _connect_with_rich_outputs("tmp/sdk-mime-truncate.ipynb")

    status = nb.status(full=True)
    bundles = status.cell(cell.id).output_mime_bundles(max_content_chars=10)

    assert bundles[0]["data"]["image/png"] == PNG_BASE64[:10]
    assert bundles[0]["data_truncated"]["image/png"] == len(PNG_BASE64)
    assert "intact payloads" in bundles[0]["hint"]


def test_output_mime_bundles_reject_summarized_outputs():
    nb, cell = _connect_with_rich_outputs("tmp/sdk-mime-summarized.ipynb")

    status = nb.status()

    with pytest.raises(HypernoteError, match="full=True"):
        status.cell(cell.id).output_mime_bundles()


def test_error_output_mime_bundle_carries_traceback_and_metadata():
    state, transport = _make_transport()
    nb = hypernote.connect(
        "tmp/sdk-mime-error.ipynb",
        create=True,
        server="http://test",
        transport=transport,
    )
    cell = nb.cells.insert_code("1 / 0", id="boom-cell")
    stored_cell = state["notebooks"]["tmp/sdk-mime-error.ipynb"]["cells"][0]
    stored_cell["outputs"] = [
        {
            "output_type": "error",
            "ename": "ZeroDivisionError",
            "evalue": "division by zero",
            "traceback": ["Traceback", "ZeroDivisionError: division by zero"],
        }
    ]

    bundles = nb.status(full=True).cell(cell.id).output_mime_bundles()

    assert bundles[0]["data"]["text/plain"] == (
        "Traceback\nZeroDivisionError: division by zero"
    )
    assert bundles[0]["metadata"]["ename"] == "ZeroDivisionError"
    assert bundles[0]["metadata"]["evalue"] == "division by zero"


def test_save_image_outputs_decodes_and_writes_files(tmp_path):
    state, transport = _make_transport()
    path = "tmp/sdk-save-images.ipynb"
    nb = hypernote.connect(path, create=True, server="http://test", transport=transport)
    cell = nb.cells.insert_code("plot()", id="plot-cell")
    stored_cell = state["notebooks"][path]["cells"][0]
    stored_cell["outputs"] = [
        {
            "output_type": "display_data",
            "data": {
                # nbformat may store base64 as list parts with trailing newlines
                "image/png": [PNG_BASE64[:12] + "\n", PNG_BASE64[12:] + "\n"],
                "image/svg+xml": SVG_TEXT,
                "text/plain": "<Figure size 640x480>",
            },
            "metadata": {},
        },
        {
            "output_type": "execute_result",
            "data": {"image/jpeg": JPEG_BASE64},
            "metadata": {},
            "execution_count": 1,
        },
        {"output_type": "stream", "name": "stdout", "text": "no image here\n"},
    ]

    status = nb.status(full=True)
    saved = status.cell(cell.id).save_image_outputs(tmp_path / "images")

    assert saved == [
        str(tmp_path / "images" / "plot-cell-out0.png"),
        str(tmp_path / "images" / "plot-cell-out0.svg"),
        str(tmp_path / "images" / "plot-cell-out1.jpg"),
    ]
    assert (tmp_path / "images" / "plot-cell-out0.png").read_bytes() == PNG_SOURCE_BYTES
    assert (tmp_path / "images" / "plot-cell-out0.svg").read_text() == SVG_TEXT
    assert (tmp_path / "images" / "plot-cell-out1.jpg").read_bytes() == JPEG_SOURCE_BYTES


def test_notebook_status_save_image_outputs_covers_all_cells(tmp_path):
    nb, _ = _connect_with_rich_outputs("tmp/sdk-save-all.ipynb")

    status = nb.status(full=True)
    saved = status.save_image_outputs(tmp_path / "all-images")

    assert saved == [str(tmp_path / "all-images" / "plot-cell-out0.png")]
    assert (tmp_path / "all-images" / "plot-cell-out0.png").read_bytes() == PNG_SOURCE_BYTES


def test_save_image_outputs_returns_empty_for_text_only_cells(tmp_path):
    _, transport = _make_transport()
    nb = hypernote.connect(
        "tmp/sdk-save-empty.ipynb",
        create=True,
        server="http://test",
        transport=transport,
    )
    cell = nb.cells.insert_code("print('hello')", id="text-cell")

    job = cell.run()
    assert job.wait().status == JobStatus.SUCCEEDED

    saved = nb.status(full=True).cell(cell.id).save_image_outputs(tmp_path / "none")
    assert saved == []


def test_save_image_outputs_wraps_malformed_base64_in_hypernote_error(tmp_path):
    state, transport = _make_transport()
    path = "tmp/sdk-save-bad-b64.ipynb"
    nb = hypernote.connect(path, create=True, server="http://test", transport=transport)
    cell = nb.cells.insert_code("plot()", id="bad-cell")
    stored_cell = state["notebooks"][path]["cells"][0]
    stored_cell["outputs"] = [
        {
            "output_type": "display_data",
            "data": {"image/png": "not%valid%base64"},
            "metadata": {},
        }
    ]

    status = nb.status(full=True)
    with pytest.raises(HypernoteError, match="Could not decode image/png"):
        status.cell(cell.id).save_image_outputs(tmp_path / "bad")


def test_save_image_outputs_writes_svg_as_utf8(tmp_path):
    state, transport = _make_transport()
    path = "tmp/sdk-save-svg-utf8.ipynb"
    nb = hypernote.connect(path, create=True, server="http://test", transport=transport)
    cell = nb.cells.insert_code("plot()", id="svg-cell")
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><text>café — 数据</text></svg>'
    stored_cell = state["notebooks"][path]["cells"][0]
    stored_cell["outputs"] = [
        {"output_type": "display_data", "data": {"image/svg+xml": svg}, "metadata": {}}
    ]

    saved = nb.status(full=True).cell(cell.id).save_image_outputs(tmp_path / "svg")

    assert saved == [str(tmp_path / "svg" / "svg-cell-out0.svg")]
    # Round-trips via UTF-8 regardless of the platform's locale encoding.
    assert (tmp_path / "svg" / "svg-cell-out0.svg").read_text(encoding="utf-8") == svg
