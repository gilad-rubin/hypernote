"""Shared helpers for live-server and browser regression tests."""

from __future__ import annotations

import asyncio
import time
import urllib.parse
import uuid
from typing import Any

import httpx


def auth_headers(token: str = "") -> dict[str, str]:
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def hypernote_headers(
    token: str = "",
    actor_id: str = "integration-agent",
    actor_type: str = "agent",
) -> dict[str, str]:
    return {
        **auth_headers(token),
        "X-Hypernote-Actor-Id": actor_id,
        "X-Hypernote-Actor-Type": actor_type,
    }


def unique_notebook_path(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}.ipynb"


def unique_workspace(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def notebook_model(*cells: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "notebook",
        "format": "json",
        "content": {
            "cells": list(cells),
            "metadata": {
                "kernelspec": {"display_name": "Python 3", "name": "python3"},
                "language_info": {"name": "python"},
                "nbformat": 4,
                "nbformat_minor": 5,
            },
            "nbformat": 4,
            "nbformat_minor": 5,
        },
    }


def code_cell(cell_id: str, source: str) -> dict[str, Any]:
    return {
        "id": cell_id,
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source,
    }


def build_lab_url(base_url: str, notebook_path: str, workspace: str, token: str = "") -> str:
    quoted_path = urllib.parse.quote(notebook_path)
    url = f"{base_url}/lab/workspaces/{workspace}/tree/{quoted_path}?reset"
    if token:
        url += f"&token={urllib.parse.quote(token)}"
    return url


def streaming_cell_source(
    *,
    cell_label: str,
    markers: list[str],
    delay_seconds: float,
) -> str:
    encoded_markers = ", ".join(_encoded_string(marker) for marker in markers)
    return "\n".join(
        [
            f"# {cell_label}",
            "import sys",
            "import time",
            f"markers = [{encoded_markers}]",
            "for marker in markers:",
            "    print(marker, flush=True)",
            "    sys.stdout.flush()",
            f"    time.sleep({delay_seconds})",
        ]
    )


def collect_stream_text(outputs: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for output in outputs:
        if output.get("output_type") == "stream":
            chunks.append(str(output.get("text", "")))
    return "".join(chunks)


def assert_markers_in_order(text: str, markers: list[str]) -> None:
    cursor = 0
    for marker in markers:
        next_cursor = text.find(marker, cursor)
        if next_cursor < 0:
            raise AssertionError(f"Marker {marker!r} not found in output text: {text!r}")
        cursor = next_cursor + len(marker)


async def create_notebook(
    client: httpx.AsyncClient,
    path: str,
    *cells: dict[str, Any],
) -> None:
    response = await client.put(
        f"/api/contents/{urllib.parse.quote(path)}",
        json=notebook_model(*cells),
    )
    response.raise_for_status()


def create_notebook_sync(
    client: httpx.Client,
    path: str,
    *cells: dict[str, Any],
) -> None:
    response = client.put(
        f"/api/contents/{urllib.parse.quote(path)}",
        json=notebook_model(*cells),
    )
    response.raise_for_status()


async def await_job(
    client: httpx.AsyncClient,
    job_id: str,
    timeout: float = 30.0,
    statuses: set[str] | None = None,
) -> dict[str, Any]:
    statuses = statuses or {"succeeded", "failed", "interrupted", "awaiting_input"}
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        response = await client.get(f"/jobs/{job_id}")
        response.raise_for_status()
        payload = response.json()
        if payload["status"] in statuses:
            return payload
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(f"Timed out waiting for job {job_id}")
        await asyncio.sleep(0.25)


async def wait_for_sdk_output(
    cell,
    marker: str,
    *,
    timeout: float = 20.0,
) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = collect_stream_text(cell.outputs)
        if marker in text:
            return text
        await asyncio.sleep(0.25)
    raise TimeoutError(f"Timed out waiting for output marker {marker!r}")


def _encoded_string(value: str) -> str:
    codepoints = ", ".join(str(ord(ch)) for ch in value)
    return f"''.join(chr(cp) for cp in [{codepoints}])"
