"""Live-server integration tests for the narrow Hypernote Phase 1 surface."""

from __future__ import annotations

import asyncio
import urllib.parse

import hypernote
from hypernote import ChangeKind
from tests.conftest import LiveServer
from tests.helpers import (
    await_job,
    code_cell,
    collect_stream_text,
    create_notebook,
    wait_for_sdk_output,
)


async def _create_live_notebook(jupyter_api, prefix: str) -> str:
    path = f"{prefix}-{asyncio.get_running_loop().time():.6f}.ipynb"
    await create_notebook(
        jupyter_api,
        path,
        code_cell("cell-a", "x = 41\nprint(x + 1)"),
        code_cell("cell-b", "name = input('Name? ')\nprint(f'hello {name}')"),
    )
    return path


async def test_execute_persists_output(
    hypernote_api,
    jupyter_api,
):
    notebook = await _create_live_notebook(jupyter_api, "hypernote-live")
    quoted = urllib.parse.quote(notebook, safe="")
    resp = await hypernote_api.post(f"/notebooks/{quoted}/execute", json={"cell_ids": ["cell-a"]})
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    job = await await_job(hypernote_api, job_id)
    assert job["status"] == "succeeded"
    assert job["request_uids"]

    model = await jupyter_api.get(f"/api/contents/{urllib.parse.quote(notebook)}")
    model.raise_for_status()
    cells = model.json()["content"]["cells"]
    outputs = cells[0]["outputs"]
    assert outputs, "Expected outputs to be persisted into the notebook"


async def test_stdin_roundtrip(hypernote_api, jupyter_api):
    notebook = await _create_live_notebook(jupyter_api, "hypernote-live")
    quoted = urllib.parse.quote(notebook, safe="")
    resp = await hypernote_api.post(f"/notebooks/{quoted}/execute", json={"cell_ids": ["cell-b"]})
    resp.raise_for_status()
    job_id = resp.json()["job_id"]

    waiting = await await_job(hypernote_api, job_id)
    assert waiting["status"] == "awaiting_input"

    send = await hypernote_api.post(f"/jobs/{job_id}/stdin", json={"value": "gilad"})
    send.raise_for_status()

    final = await await_job(
        hypernote_api,
        job_id,
        timeout=60.0,
        statuses={"running", "succeeded", "failed", "interrupted", "awaiting_input"},
    )
    assert final["status"] in {"running", "succeeded", "failed", "interrupted"}


async def test_runtime_open_and_stop(hypernote_api, jupyter_api):
    notebook = await _create_live_notebook(jupyter_api, "hypernote-live")
    quoted = urllib.parse.quote(notebook, safe="")
    opened = await hypernote_api.post(
        f"/notebooks/{quoted}/runtime/open",
        json={"client_id": "integration-cli"},
    )
    opened.raise_for_status()
    assert opened.json()["state"] == "live-attached"

    status = await hypernote_api.get(f"/notebooks/{quoted}/runtime")
    status.raise_for_status()
    assert status.json()["kernel_id"] is not None

    stopped = await hypernote_api.post(f"/notebooks/{quoted}/runtime/stop", json={})
    stopped.raise_for_status()
    assert stopped.json()["state"] == "stopped"


async def test_document_insert_execute_roundtrip(
    hypernote_api,
    jupyter_api,
):
    notebook = await _create_live_notebook(jupyter_api, "hypernote-live")
    quoted = urllib.parse.quote(notebook, safe="")
    inserted = await hypernote_api.post(
        f"/notebooks/{quoted}/cells",
        json={
            "id": "cell-inserted",
            "cell_type": "code",
            "source": "y = 20 + 22\nprint(y)",
            "after": "cell-a",
        },
    )
    inserted.raise_for_status()
    assert inserted.json()["cell"]["id"] == "cell-inserted"

    resp = await hypernote_api.post(
        f"/notebooks/{quoted}/execute",
        json={"cell_ids": ["cell-inserted"]},
    )
    resp.raise_for_status()
    job_id = resp.json()["job_id"]

    job = await await_job(hypernote_api, job_id)
    assert job["status"] == "succeeded"

    model = await hypernote_api.get(f"/notebooks/{quoted}/document")
    model.raise_for_status()
    cells = model.json()["content"]["cells"]
    inserted_cell = next(cell for cell in cells if cell["id"] == "cell-inserted")
    assert inserted_cell["execution_count"] is not None
    assert inserted_cell["outputs"], "Expected inserted cell outputs to persist"


async def test_sdk_diff_reports_add_edit_and_execution_changes(
    jupyter_api,
    live_server: LiveServer,
):
    notebook = await _create_live_notebook(jupyter_api, "hypernote-live")
    nb = await asyncio.to_thread(
        hypernote.connect,
        notebook,
        False,
        server=live_server.base_url,
        token=live_server.token or None,
    )

    baseline = await asyncio.to_thread(nb.snapshot)

    inserted = await asyncio.to_thread(
        nb.cells.insert_code,
        "value = 1\nprint(value)",
        id="cell-added",
        after="cell-a",
    )
    added = await asyncio.to_thread(nb.diff, snapshot=baseline, full=True)
    added_by_id = {cell.id: cell for cell in added.cells}
    assert "cell-added" in added_by_id
    assert added_by_id["cell-added"].change_kinds == (ChangeKind.ADDED,)

    edit_baseline = await asyncio.to_thread(nb.snapshot)
    await asyncio.to_thread(inserted.replace, "value = 2\nprint(value)")
    edited = await asyncio.to_thread(nb.diff, snapshot=edit_baseline, full=True)
    assert len(edited.cells) == 1
    assert edited.cells[0].id == "cell-added"
    assert ChangeKind.SOURCE_EDITED in edited.cells[0].change_kinds

    run_baseline = await asyncio.to_thread(nb.snapshot)
    job = await asyncio.to_thread(inserted.run)
    await asyncio.to_thread(job.wait, 30)
    executed = await asyncio.to_thread(nb.diff, snapshot=run_baseline, full=True)
    assert len(executed.cells) == 1
    assert executed.cells[0].id == "cell-added"
    assert set(executed.cells[0].change_kinds) & {
        ChangeKind.OUTPUT_CHANGED,
        ChangeKind.EXECUTION_COUNT,
    }


async def test_inserted_cell_runs_immediately_with_live_runtime(
    jupyter_api,
    live_server: LiveServer,
):
    notebook = await _create_live_notebook(jupyter_api, "hypernote-live")
    nb = await asyncio.to_thread(
        hypernote.connect,
        notebook,
        False,
        server=live_server.base_url,
        token=live_server.token or None,
    )

    runtime = await asyncio.to_thread(nb.runtime.ensure)
    assert runtime.status.value == "live-attached"

    source = "\n".join(
        [
            "parts = [[108, 105, 118, 101], [45], [114, 117, 110]]",
            "print(''.join(chr(code) for group in parts for code in group))",
        ]
    )
    inserted = await asyncio.to_thread(
        nb.cells.insert_code,
        source,
        id="cell-live-runtime",
        after="cell-a",
    )

    job = await asyncio.to_thread(inserted.run)
    await asyncio.to_thread(job.wait, 30)
    await wait_for_sdk_output(inserted, "live-run", timeout=10)

    stream_text = collect_stream_text(inserted.outputs)
    assert "live-run" in stream_text
    assert inserted.execution_count is not None
