"""Browser regression tests for Hypernote's single-truth notebook behavior."""

from __future__ import annotations

import time

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

    cell = _wait_for_cell(page, cell_label)
    _wait_for_condition(lambda: markers[0] in _cell_text(cell), timeout=2.5)

    job.wait(timeout=30)
    _wait_for_condition(lambda: markers[-1] in _cell_text(cell), timeout=15)

    final_text = collect_stream_text(inserted.outputs)
    assert_markers_in_order(final_text, markers)
    for marker in markers:
        assert final_text.count(marker) == 1


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
