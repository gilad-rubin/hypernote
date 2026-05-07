"""Browser regression tests for Hypernote's single-truth notebook behavior."""

from __future__ import annotations

import time
import urllib.parse

import httpx
import pytest
from playwright.sync_api import sync_playwright

import hypernote
from tests.conftest import LiveServer
from tests.helpers import (
    assert_markers_in_order,
    auth_headers,
    build_lab_url,
    code_cell,
    collect_stream_text,
    create_notebook_sync,
    streaming_cell_source,
    unique_notebook_path,
    unique_workspace,
)


@pytest.fixture
def page():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 960})
        try:
            yield context.new_page()
        finally:
            context.close()
            browser.close()


def test_open_tab_streaming_regression(page, live_server: LiveServer):
    notebook_path = unique_notebook_path("hypernote-browser-open")
    workspace = unique_workspace("open-tab")
    cell_id = "stream-open-tab"
    cell_label = f"hypernote-open-tab-{cell_id}"
    markers = ["open-stream-one", "open-stream-two", "open-stream-final"]

    with httpx.Client(
        base_url=live_server.base_url,
        headers=auth_headers(live_server.token),
        timeout=30,
    ) as client:
        create_notebook_sync(
            client,
            notebook_path,
            code_cell("cell-a", "print(41 + 1)"),
        )

    nb = hypernote.connect(
        notebook_path,
        server=live_server.base_url,
        token=live_server.token or None,
    )
    source = streaming_cell_source(
        cell_label=cell_label,
        markers=markers,
        delay_seconds=2.0,
    )

    page.goto(
        build_lab_url(live_server.base_url, notebook_path, workspace, live_server.token),
        wait_until="domcontentloaded",
    )
    page.wait_for_selector(".jp-Notebook", timeout=30000)
    _wait_for_cell(page, "print(41 + 1)")
    page.wait_for_timeout(1000)

    inserted = nb.cells.insert_code(source, id=cell_id, after="cell-a")
    cell = _wait_for_cell(page, cell_label)

    job = inserted.run()
    _wait_for_condition(lambda: _job_status(job) in {"running", "succeeded"}, timeout=10)
    _wait_for_condition(lambda: _cell_shows_running_state(cell), timeout=10)

    _wait_for_condition(lambda: markers[0] in _cell_text(cell), timeout=15)
    first_text = _cell_text(cell)
    assert markers[1] not in first_text

    _wait_for_condition(lambda: markers[1] in _cell_text(cell), timeout=15)
    second_text = _cell_text(cell)
    assert markers[0] in second_text

    job.wait(timeout=30)
    _wait_for_condition(lambda: markers[-1] in _cell_text(cell), timeout=15)

    final_text = collect_stream_text(inserted.outputs)
    assert_markers_in_order(final_text, markers)
    for marker in markers:
        assert final_text.count(marker) == 1


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_late_open_streaming_replays_prior_output_without_restart(page, live_server: LiveServer):
    notebook_path = unique_notebook_path("hypernote-browser-late")
    workspace = unique_workspace("late-open")
    cell_id = "stream-late-open"
    cell_label = f"hypernote-late-open-{cell_id}"
    markers = [
        "late-stream-one",
        "late-stream-two",
        "late-stream-three",
        "late-stream-four",
        "late-stream-five",
        "late-stream-six",
        "late-stream-seven",
        "late-stream-eight",
        "late-stream-nine",
        "late-stream-final",
    ]

    with httpx.Client(
        base_url=live_server.base_url,
        headers=auth_headers(live_server.token),
        timeout=30,
    ) as client:
        create_notebook_sync(
            client,
            notebook_path,
            code_cell("cell-a", "print(6 * 7)"),
        )

    nb = hypernote.connect(
        notebook_path,
        server=live_server.base_url,
        token=live_server.token or None,
    )
    source = streaming_cell_source(
        cell_label=cell_label,
        markers=markers,
        delay_seconds=4.0,
    )
    inserted = nb.cells.insert_code(source, id=cell_id, after="cell-a")

    job = inserted.run()
    _wait_for_condition(lambda: _job_status(job) in {"running", "succeeded"}, timeout=10)
    _wait_for_condition(
        lambda: markers[0] in collect_stream_text(inserted.outputs),
        timeout=15,
    )
    assert job.id

    page.goto(
        build_lab_url(live_server.base_url, notebook_path, workspace, live_server.token),
        wait_until="domcontentloaded",
    )
    page.wait_for_selector(".jp-Notebook", timeout=30000)
    _wait_for_cell(page, "print(6 * 7)")
    assert _job_status(job) == "running", (
        "Lab finished rendering only after the job completed — late-open did not "
        "actually catch mid-run state. The notebook UI was likely blocked on a "
        "kernel-info handshake against a busy main shell."
    )

    cell = _wait_for_cell(page, cell_label)
    _wait_for_condition(lambda: markers[0] in _cell_text(cell), timeout=2.5)
    assert markers[-1] not in _cell_text(cell), (
        "Final marker already in cell at first render — Lab rendered post-completion, "
        "not mid-run."
    )

    job.wait(timeout=60)
    _wait_for_condition(lambda: markers[-1] in _cell_text(cell), timeout=15)

    final_text = collect_stream_text(inserted.outputs)
    assert_markers_in_order(final_text, markers)
    for marker in markers:
        assert final_text.count(marker) == 1


def test_lab_interrupt_button_stops_subshell_routed_cell(page, live_server: LiveServer):
    """Lab's kernel-interrupt action terminates a Hypernote-driven cell.

    Hypernote routes execute_request through an ipykernel subshell so concurrent
    clients (Lab) can talk to the kernel mid-run. The trade-off is that
    process-wide SIGINT does not reach the subshell thread. The Hypernote
    extension overrides Jupyter Server's /api/kernels/{id}/interrupt route to
    raise KeyboardInterrupt in the subshell thread via PyThreadState_SetAsyncExc.

    This test proves that a JupyterLab kernel-interrupt action (the same backend
    as the Stop button) actually stops the cell. We use the I,I keyboard
    shortcut because it dispatches the same `notebook:interrupt-kernel` command
    the toolbar button does, and is reliable across Lab versions.
    """
    notebook_path = unique_notebook_path("hypernote-browser-interrupt")
    workspace = unique_workspace("interrupt")
    cell_id = "long-interrupt"
    cell_label = f"hypernote-interrupt-{cell_id}"

    with httpx.Client(
        base_url=live_server.base_url,
        headers=auth_headers(live_server.token),
        timeout=30,
    ) as client:
        create_notebook_sync(
            client,
            notebook_path,
            code_cell("cell-a", "print(1)"),
        )

    nb = hypernote.connect(
        notebook_path,
        server=live_server.base_url,
        token=live_server.token or None,
    )
    # 60s of streaming; we only need it long enough that the cell is
    # mid-execute when the interrupt arrives.
    source = streaming_cell_source(
        cell_label=cell_label,
        markers=[f"int-tick-{i}" for i in range(60)] + ["int-final"],
        delay_seconds=1.0,
    )
    inserted = nb.cells.insert_code(source, id=cell_id, after="cell-a")

    job = inserted.run()
    _wait_for_condition(lambda: _job_status(job) in {"running", "succeeded"}, timeout=10)

    page.goto(
        build_lab_url(live_server.base_url, notebook_path, workspace, live_server.token),
        wait_until="domcontentloaded",
    )
    page.wait_for_selector(".jp-Notebook", state="visible", timeout=30000)
    cell = _wait_for_cell(page, cell_label)
    _wait_for_condition(lambda: "int-tick-2" in _cell_text(cell), timeout=20)
    assert _job_status(job) == "running", (
        "job already finished before interrupt could be tested"
    )

    # Dispatch Lab's kernel-interrupt command via the I,I keyboard shortcut.
    # Equivalent to clicking the Stop button: both call
    # `notebook:interrupt-kernel`, which posts to /api/kernels/{id}/interrupt —
    # the route Hypernote overrides.
    cell.click()
    page.keyboard.press("Escape")
    page.keyboard.press("KeyI")
    page.keyboard.press("KeyI")

    # Job should transition out of "running" within a couple of seconds.
    _wait_for_condition(
        lambda: _job_status(job) in {"failed", "succeeded"},
        timeout=5,
    )
    assert _job_status(job) == "failed", "interrupt did not raise an error in the cell"

    # Cell content should reflect KeyboardInterrupt.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if "KeyboardInterrupt" in _cell_text(cell):
            break
        time.sleep(0.2)
    else:
        raise AssertionError(
            "cell did not show KeyboardInterrupt after job entered 'failed'"
        )


def test_lab_restart_button_lets_subsequent_cells_run(page, live_server: LiveServer):
    """A POST to /api/kernels/{id}/restart (Lab's Restart button) cleans up
    Hypernote's subshell state and nbmodel's stale kernel client, so the
    next execute succeeds rather than hanging on a dead worker.

    Lab restart bypasses the autorestarter callbacks; without the
    `KernelRestartInterceptHandler` override the test would either time
    out (worker stuck on dead channels) or fail with a stale subshell_id.
    """
    notebook_path = unique_notebook_path("hypernote-browser-restart")
    workspace = unique_workspace("restart")

    with httpx.Client(
        base_url=live_server.base_url,
        headers=auth_headers(live_server.token),
        timeout=30,
    ) as client:
        create_notebook_sync(
            client,
            notebook_path,
            code_cell("cell-a", "x = 42; print('first')"),
        )

    nb = hypernote.connect(
        notebook_path,
        server=live_server.base_url,
        token=live_server.token or None,
    )

    # 1. Run a cell so the runtime, kernel, subshell, and nbmodel client are
    #    all warmed up.
    first_job = nb.cells["cell-a"].run()
    first_job.wait(timeout=30)
    assert first_job.refresh().status.value == "succeeded"

    # 2. Open Lab so we have a real client attached to the YDoc — restart
    #    in real life is initiated from a connected Lab tab.
    page.goto(
        build_lab_url(live_server.base_url, notebook_path, workspace, live_server.token),
        wait_until="domcontentloaded",
    )
    page.wait_for_selector(".jp-Notebook", state="visible", timeout=30000)
    _wait_for_cell(page, "x = 42")

    # 3. Trigger the same route Lab's Restart button hits. Done over HTTP
    #    rather than the keyboard shortcut so the assertion is precise about
    #    which path is exercised. URL-encode notebook_path so a unique-id
    #    value containing slashes or other reserved characters does not
    #    split the path into segments.
    encoded_notebook = urllib.parse.quote(notebook_path, safe="")
    runtime_status = httpx.get(
        f"{live_server.base_url}/hypernote/api/notebooks/{encoded_notebook}/runtime",
        headers=auth_headers(live_server.token),
    ).json()
    kernel_id = runtime_status["kernel_id"]
    restart_resp = httpx.post(
        f"{live_server.base_url}/api/kernels/{kernel_id}/restart",
        headers=auth_headers(live_server.token),
        timeout=30,
    )
    assert restart_resp.status_code == 200, restart_resp.text

    # 4. The decisive step — insert a NEW cell and run it. If restart
    #    cleanup did not happen, this hangs because nbmodel's worker is
    #    stuck on the previous (now-dead) kernel client.
    second = nb.cells.insert_code("print('after-restart')", id="cell-b", after="cell-a")
    second_job = second.run()
    second_job.wait(timeout=30)
    assert second_job.refresh().status.value == "succeeded"

    chunks = []
    for output in second.outputs:
        if output.get("output_type") != "stream":
            continue
        text = output.get("text", "")
        chunks.append(text if isinstance(text, str) else "".join(text))
    assert "after-restart" in "".join(chunks)


def _wait_for_cell(page, cell_label: str):
    locator = page.locator(".jp-CodeCell").filter(has_text=cell_label).last
    locator.wait_for(timeout=30000)
    return locator


def _cell_text(cell) -> str:
    return cell.text_content() or ""


def _cell_shows_running_state(cell) -> bool:
    payload = cell.evaluate(
        """
        (node) => {
          const prompt = node.querySelector('.jp-InputPrompt')?.textContent || '';
          return {
            prompt,
            classes: Array.from(node.classList),
            ariaBusy: node.getAttribute('aria-busy') || '',
          };
        }
        """
    )
    prompt = payload["prompt"]
    classes = " ".join(payload["classes"]).lower()
    return (
        "*" in prompt
        or "running" in classes
        or "execut" in classes
        or payload["ariaBusy"].lower() == "true"
    )


def _job_status(job: hypernote.Job) -> str:
    return job.refresh().status.value


def _wait_for_condition(callback, *, timeout: float, interval: float = 0.2) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callback():
            return
        time.sleep(interval)
    raise TimeoutError("Timed out waiting for browser regression condition")
