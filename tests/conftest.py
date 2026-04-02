"""Pytest fixtures for live Hypernote integration and browser tests."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from tests.helpers import auth_headers, hypernote_headers


@dataclass(frozen=True)
class LiveServer:
    base_url: str
    root_dir: Path
    token: str = ""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def live_server(tmp_path_factory: pytest.TempPathFactory) -> LiveServer:
    root_dir = tmp_path_factory.mktemp("jupyter-root")
    log_path = root_dir / "jupyter.log"
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    cmd = [
        sys.executable,
        "-m",
        "jupyterlab",
        "--no-browser",
        f"--ServerApp.port={port}",
        "--ServerApp.port_retries=0",
        "--ServerApp.token=",
        "--ServerApp.password=",
        "--ServerApp.disable_check_xsrf=True",
        f"--ServerApp.root_dir={root_dir}",
        (
            "--ServerApp.jpserver_extensions="
            "{'hypernote': True, 'jupyter_server_nbmodel': True, 'jupyter_server_ydoc': True}"
        ),
    ]
    env = os.environ.copy()
    process = subprocess.Popen(
        cmd,
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        stdout=log_path.open("w"),
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    "JupyterLab exited during startup.\n" + log_path.read_text(errors="ignore")
                )
            try:
                response = httpx.get(f"{base_url}/hypernote/api/jobs", timeout=1)
                if response.status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError(
                "Timed out waiting for JupyterLab startup.\n"
                + log_path.read_text(errors="ignore")
            )

        yield LiveServer(base_url=base_url, root_dir=root_dir)
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=15)


@pytest.fixture
async def jupyter_api(live_server: LiveServer):
    async with httpx.AsyncClient(
        base_url=live_server.base_url,
        headers=auth_headers(live_server.token),
        timeout=30,
    ) as client:
        yield client


@pytest.fixture
async def hypernote_api(live_server: LiveServer):
    async with httpx.AsyncClient(
        base_url=f"{live_server.base_url}/hypernote/api",
        headers=hypernote_headers(live_server.token),
        timeout=30,
    ) as client:
        yield client
