"""Tests for the agent-first Hypernote CLI."""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from hypernote import CellType, ChangeKind, JobStatus, RuntimeStatus
from hypernote.cli import main as cli_main
from hypernote.cli.main import cli


class FakeJob:
    def __init__(
        self,
        notebook: "FakeNotebook",
        cell_ids: tuple[str, ...],
        transitions: list[dict] | None = None,
    ):
        self.notebook = notebook
        self.id = f"job-{len(notebook.created_jobs) + 1}"
        self.status = JobStatus.RUNNING if transitions else JobStatus.SUCCEEDED
        self.cell_ids = cell_ids
        self.notebook_path = notebook.path
        self._transitions = list(transitions or [])

    def refresh(self) -> "FakeJob":
        if self._transitions:
            transition = self._transitions.pop(0)
            for cell_id, outputs in transition.get("outputs", {}).items():
                self.notebook._cells[cell_id]["outputs"] = outputs
            for cell_id, execution_count in transition.get("execution_counts", {}).items():
                self.notebook._cells[cell_id]["execution_count"] = execution_count
            if "status" in transition:
                self.status = transition["status"]
        return self

    def wait(self, timeout: float | None = None) -> "FakeJob":  # noqa: ARG002
        while self.status not in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.INTERRUPTED,
            JobStatus.AWAITING_INPUT,
        }:
            self.refresh()
        return self

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status.value,
            "cell_ids": list(self.cell_ids),
            "notebook_path": self.notebook_path,
        }


class FakeTimeoutJob(FakeJob):
    def wait(self, timeout: float | None = None) -> "FakeJob":  # noqa: ARG002
        raise cli_main.ExecutionTimeoutError(cli_main._job_timeout_message(self))


class FakeRuntime:
    def __init__(self, notebook: "FakeNotebook"):
        self.notebook = notebook
        self.status = RuntimeStatus.STOPPED

    def ensure(self) -> "FakeRuntime":
        self.status = RuntimeStatus.LIVE_ATTACHED
        return self

    def stop(self) -> "FakeRuntime":
        self.status = RuntimeStatus.STOPPED
        return self

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "recoverable": False,
            "session_id": None,
            "kernel_id": None,
            "kernel_name": "python3",
        }


class FakeCellHandle:
    def __init__(self, notebook: "FakeNotebook", cell_id: str):
        self._notebook = notebook
        self.id = cell_id

    @property
    def _cell(self) -> dict:
        return self._notebook._cells[self.id]

    @property
    def type(self) -> CellType:
        return CellType(self._cell["cell_type"])

    @property
    def source(self) -> str:
        return self._cell["source"]

    @property
    def outputs(self) -> tuple[dict, ...]:
        return tuple(self._cell.get("outputs", []))

    @property
    def execution_count(self) -> int | None:
        return self._cell.get("execution_count")

    def replace(self, source: str) -> "FakeCellHandle":
        self._cell["source"] = source
        return self

    def delete(self) -> None:
        self._notebook._order.remove(self.id)
        self._notebook._cells.pop(self.id, None)

    def move(self, *, before: str | None = None, after: str | None = None) -> None:
        self._notebook._order.remove(self.id)
        if before is not None:
            index = self._notebook._order.index(before)
            self._notebook._order.insert(index, self.id)
        elif after is not None:
            index = self._notebook._order.index(after) + 1
            self._notebook._order.insert(index, self.id)
        else:
            self._notebook._order.append(self.id)

    def clear_outputs(self) -> "FakeCellHandle":
        self._cell["outputs"] = []
        return self

    def run(self) -> FakeJob:
        if self.type != CellType.CODE:
            raise cli_main.click.ClickException(f"Cell {self.id} is {self.type.value}, not code")
        return self._notebook._create_job((self.id,))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "source": self.source,
            "outputs": list(self.outputs),
            "execution_count": self.execution_count,
        }


class FakeCellCollection:
    def __init__(self, notebook: "FakeNotebook"):
        self._notebook = notebook

    def __getitem__(self, cell_id: str) -> FakeCellHandle:
        if cell_id not in self._notebook._cells:
            raise cli_main.CellNotFoundError(cell_id)
        return FakeCellHandle(self._notebook, cell_id)

    def __iter__(self):
        for cell_id in self._notebook._order:
            yield FakeCellHandle(self._notebook, cell_id)

    def insert_code(
        self,
        source: str,
        *,
        id: str | None = None,
        before: str | None = None,
        after: str | None = None,
    ) -> FakeCellHandle:
        return self._insert("code", source, id=id, before=before, after=after)

    def insert_markdown(
        self,
        source: str,
        *,
        id: str | None = None,
        before: str | None = None,
        after: str | None = None,
    ) -> FakeCellHandle:
        return self._insert("markdown", source, id=id, before=before, after=after)

    def _insert(
        self,
        cell_type: str,
        source: str,
        *,
        id: str | None,
        before: str | None,
        after: str | None,
    ) -> FakeCellHandle:
        cell_id = id or f"cell-{len(self._notebook._order) + 1}"
        self._notebook._cells[cell_id] = {
            "id": cell_id,
            "cell_type": cell_type,
            "source": source,
            "outputs": [],
            "execution_count": None,
        }
        if before is not None:
            index = self._notebook._order.index(before)
            self._notebook._order.insert(index, cell_id)
        elif after is not None:
            index = self._notebook._order.index(after) + 1
            self._notebook._order.insert(index, cell_id)
        else:
            self._notebook._order.append(cell_id)
        return FakeCellHandle(self._notebook, cell_id)


@dataclass
class FakeCellStatus:
    id: str
    type: CellType
    source: str
    outputs: tuple[dict, ...]
    execution_count: int | None
    change_kinds: tuple[ChangeKind, ...] = ()


class FakeNotebookStatus:
    def __init__(self, notebook: "FakeNotebook", *, diff: bool = False):
        self.notebook_path = notebook.path
        self.summary = f"{notebook.path} · {'diff' if diff else 'status'}"
        self.current = SimpleNamespace(token="snap-123")
        self.cells = tuple(
            FakeCellStatus(
                id=cell_id,
                type=CellType(notebook._cells[cell_id]["cell_type"]),
                source=notebook._cells[cell_id]["source"],
                outputs=tuple(notebook._cells[cell_id]["outputs"]),
                execution_count=notebook._cells[cell_id]["execution_count"],
                change_kinds=((ChangeKind.ADDED,) if diff else ()),
            )
            for cell_id in notebook._order
        )

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "cells": [
                {
                    "id": cell.id,
                    "type": cell.type.value,
                    "source": cell.source,
                    "outputs": list(cell.outputs),
                    "execution_count": cell.execution_count,
                    "change_kinds": [kind.value for kind in cell.change_kinds],
                }
                for cell in self.cells
            ],
        }


class FakeNotebook:
    def __init__(self, path: str):
        self.path = path
        self.runtime = FakeRuntime(self)
        self.cells = FakeCellCollection(self)
        self._cells: dict[str, dict] = {}
        self._order: list[str] = []
        self.created_jobs: list[FakeJob] = []
        self.next_job_transitions: list[list[dict]] = []
        self.interrupted = False
        self.restarted = False

    def _create_job(self, cell_ids: tuple[str, ...]) -> FakeJob:
        transitions = self.next_job_transitions.pop(0) if self.next_job_transitions else None
        job = FakeJob(self, cell_ids, transitions)
        self.created_jobs.append(job)
        return job

    def run(self, *cell_ids: str) -> FakeJob:
        return self._create_job(tuple(cell_ids))

    def run_all(self) -> FakeJob:
        code_ids = tuple(
            cell_id for cell_id in self._order if self._cells[cell_id]["cell_type"] == "code"
        )
        return self._create_job(code_ids)

    def restart(self) -> FakeRuntime:
        self.restarted = True
        self.runtime.status = RuntimeStatus.LIVE_ATTACHED
        return self.runtime

    def interrupt(self) -> None:
        self.interrupted = True

    def status(self, *, full: bool = False) -> FakeNotebookStatus:  # noqa: ARG002
        return FakeNotebookStatus(self, diff=False)

    def diff(self, *, snapshot, full: bool = False) -> FakeNotebookStatus:  # noqa: ARG002
        return FakeNotebookStatus(self, diff=True)


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def fake_notebooks(monkeypatch):
    notebooks: dict[str, FakeNotebook] = {}

    def factory(path: str, create: bool = False, **kwargs):  # noqa: ARG001
        if path not in notebooks and not create:
            raise cli_main.NotebookNotFoundError(path)
        notebooks.setdefault(path, FakeNotebook(path))
        return notebooks[path]

    monkeypatch.setattr(cli_main, "connect", factory)
    return notebooks


def test_cli_help(runner):
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for cmd in [
        "create",
        "ix",
        "exec",
        "edit",
        "run-all",
        "restart",
        "restart-run-all",
        "interrupt",
        "status",
        "diff",
        "cat",
        "job",
        "runtime",
        "setup",
    ]:
        assert cmd in result.output


def test_ix_non_tty_returns_compact_json_by_default(runner, fake_notebooks, monkeypatch):
    monkeypatch.setattr(cli_main, "_stdout_is_tty", lambda: False)
    nb = fake_notebooks.setdefault("demo.ipynb", FakeNotebook("demo.ipynb"))
    nb.next_job_transitions.append(
        [{"status": JobStatus.SUCCEEDED, "execution_counts": {"cell-1": 1}}]
    )

    result = runner.invoke(cli, ["ix", "demo.ipynb", "-s", "print(1)"])

    assert result.exit_code == 0
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["command"] == "ix"
    assert payload["job"]["status"] == "succeeded"
    assert payload["inserted_cells"][0]["id"] == "cell-1"


def test_ix_tty_streams_human_progress_by_default(runner, fake_notebooks, monkeypatch):
    monkeypatch.setattr(cli_main, "_stdout_is_tty", lambda: True)
    nb = fake_notebooks.setdefault("demo.ipynb", FakeNotebook("demo.ipynb"))
    nb.next_job_transitions.append(
        [
            {
                "status": JobStatus.RUNNING,
                "outputs": {
                    "cell-1": [{"output_type": "stream", "name": "stdout", "text": "42\n"}]
                },
            },
            {
                "status": JobStatus.SUCCEEDED,
                "outputs": {
                    "cell-1": [{"output_type": "stream", "name": "stdout", "text": "42\n"}]
                },
                "execution_counts": {"cell-1": 1},
            },
        ]
    )

    result = runner.invoke(cli, ["ix", "demo.ipynb", "-s", "print(42)"])

    assert result.exit_code == 0
    assert "Started job" in result.output
    assert "42" in result.output
    assert "succeeded" in result.output


def test_ix_stream_json_emits_events(runner, fake_notebooks, monkeypatch):
    monkeypatch.setattr(cli_main, "_stdout_is_tty", lambda: False)
    nb = fake_notebooks.setdefault("demo.ipynb", FakeNotebook("demo.ipynb"))
    nb.next_job_transitions.append(
        [
            {
                "status": JobStatus.RUNNING,
                "outputs": {
                    "cell-1": [{"output_type": "stream", "name": "stdout", "text": "tick:0\n"}]
                },
            },
            {
                "status": JobStatus.SUCCEEDED,
                "outputs": {
                    "cell-1": [{"output_type": "stream", "name": "stdout", "text": "tick:0\n"}]
                },
                "execution_counts": {"cell-1": 1},
            },
        ]
    )

    result = runner.invoke(cli, ["ix", "demo.ipynb", "-s", "print(1)", "--stream-json"])

    assert result.exit_code == 0
    events = [json.loads(line) for line in result.output.strip().splitlines()]
    event_names = [event["event"] for event in events]
    assert "cell_inserted" in event_names
    assert "job_started" in event_names
    assert "output_delta" in event_names
    assert "job_completed" in event_names


def test_create_empty_removes_default_cells(runner, fake_notebooks, monkeypatch):
    monkeypatch.setattr(cli_main, "_stdout_is_tty", lambda: False)
    nb = fake_notebooks.setdefault("new.ipynb", FakeNotebook("new.ipynb"))
    nb.cells.insert_code("", id="default-blank")

    result = runner.invoke(cli, ["create", "new.ipynb", "--empty"])

    assert result.exit_code == 0
    assert len(nb._order) == 0
    payload = json.loads(result.output)
    assert payload["command"] == "create"


def test_exec_on_markdown_cell_fails_clearly(runner, fake_notebooks, monkeypatch):
    monkeypatch.setattr(cli_main, "_stdout_is_tty", lambda: False)
    nb = fake_notebooks.setdefault("demo.ipynb", FakeNotebook("demo.ipynb"))
    nb.cells.insert_markdown("hello", id="md-1")

    result = runner.invoke(cli, ["exec", "demo.ipynb", "md-1"])

    assert result.exit_code != 0
    assert "not code" in result.output


def test_batch_ix_no_wait_is_rejected(runner, fake_notebooks, monkeypatch):
    monkeypatch.setattr(cli_main, "_stdout_is_tty", lambda: False)
    fake_notebooks.setdefault("demo.ipynb", FakeNotebook("demo.ipynb"))

    result = runner.invoke(
        cli,
        [
            "ix",
            "demo.ipynb",
            "--cells-json",
            json.dumps(
                [
                    {"type": "markdown", "source": "# Title"},
                    {"type": "code", "source": "print(1)"},
                ]
            ),
            "--no-wait",
        ],
    )

    assert result.exit_code != 0
    assert "batch ix does not support --no-wait" in result.output


def test_batch_ix_reports_partial_state_after_failure(runner, fake_notebooks, monkeypatch):
    monkeypatch.setattr(cli_main, "_stdout_is_tty", lambda: False)
    nb = fake_notebooks.setdefault("demo.ipynb", FakeNotebook("demo.ipynb"))
    nb.next_job_transitions.append(
        [
            {
                "status": JobStatus.FAILED,
                "execution_counts": {"setup-cell": 1},
            }
        ]
    )

    result = runner.invoke(
        cli,
        [
            "ix",
            "demo.ipynb",
            "--cells-json",
            json.dumps(
                [
                    {"id": "intro-md", "type": "markdown", "source": "# Intro"},
                    {"id": "setup-cell", "type": "code", "source": "raise RuntimeError('boom')"},
                    {"id": "later-cell", "type": "code", "source": "print('later')"},
                ]
            ),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["status"] == "error"
    assert payload["halt_reason"] == "job_failed"
    assert payload["last_processed_cell_id"] == "setup-cell"
    assert payload["cells_inserted"] == 2
    assert payload["cells_total"] == 3
    assert payload["cells_remaining"] == 1
    assert [entry["cell_id"] for entry in payload["results"]] == ["intro-md", "setup-cell"]


def test_edit_replace_maps_to_sdk_mutation(runner, fake_notebooks, monkeypatch):
    monkeypatch.setattr(cli_main, "_stdout_is_tty", lambda: False)
    nb = fake_notebooks.setdefault("demo.ipynb", FakeNotebook("demo.ipynb"))
    nb.cells.insert_code("print(1)", id="code-1")

    result = runner.invoke(cli, ["edit", "replace", "demo.ipynb", "code-1", "-s", "print(2)"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["cell"]["source"] == "print(2)"


def test_status_and_diff_surface_observation_model(runner, fake_notebooks, monkeypatch):
    monkeypatch.setattr(cli_main, "_stdout_is_tty", lambda: False)
    nb = fake_notebooks.setdefault("demo.ipynb", FakeNotebook("demo.ipynb"))
    nb.cells.insert_code("print(1)", id="code-1")

    status_result = runner.invoke(cli, ["status", "demo.ipynb"])
    diff_result = runner.invoke(cli, ["diff", "demo.ipynb", "--snapshot", "snap-123"])

    assert status_result.exit_code == 0
    assert diff_result.exit_code == 0
    assert json.loads(status_result.output)["command"] == "status"
    assert json.loads(diff_result.output)["command"] == "diff"


def test_job_get_and_stdin_use_sdk_control(runner, monkeypatch):
    monkeypatch.setattr(cli_main, "_stdout_is_tty", lambda: False)

    class FakeControl:
        def get_job_payload(self, job_id: str) -> dict:
            return {"job_id": job_id, "status": "running"}

        def send_job_stdin(self, job_id: str, value: str) -> dict:
            return {"job_id": job_id, "sent": True, "value": value}

    monkeypatch.setattr(cli_main, "_sdk_control", lambda ctx: FakeControl())

    get_result = runner.invoke(cli, ["job", "get", "job-1"])
    stdin_result = runner.invoke(cli, ["job", "stdin", "job-1", "--value", "gilad"])

    assert get_result.exit_code == 0
    assert stdin_result.exit_code == 0
    assert json.loads(get_result.output)["job_id"] == "job-1"
    assert json.loads(stdin_result.output)["sent"] is True


def test_job_await_uses_sdk_control_lookup(runner, fake_notebooks, monkeypatch):
    monkeypatch.setattr(cli_main, "_stdout_is_tty", lambda: False)
    nb = fake_notebooks.setdefault("demo.ipynb", FakeNotebook("demo.ipynb"))
    nb.cells.insert_code("print(1)", id="code-1")
    job = nb._create_job(("code-1",))
    job.status = JobStatus.SUCCEEDED

    class FakeControl:
        def get_job(self, job_id: str) -> FakeJob:
            assert job_id == "job-1"
            return job

    monkeypatch.setattr(cli_main, "_sdk_control", lambda ctx: FakeControl())

    result = runner.invoke(cli, ["job", "await", "job-1"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["command"] == "job.await"
    assert payload["job"]["status"] == "succeeded"


def test_setup_doctor_uses_sdk_control(runner, monkeypatch):
    monkeypatch.setattr(cli_main, "_stdout_is_tty", lambda: False)

    class FakeControl:
        def list_jobs(self) -> dict:
            return {"jobs": []}

    monkeypatch.setattr(cli_main, "_sdk_control", lambda ctx: FakeControl())

    result = runner.invoke(cli, ["setup", "doctor"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["hypernote_api"] == "ok"
    assert payload["jobs_endpoint"] is True


def test_setup_doctor_reports_default_kernel_launcher(runner, monkeypatch):
    monkeypatch.setattr(cli_main, "_stdout_is_tty", lambda: False)

    class FakeControl:
        def list_jobs(self) -> dict:
            return {"jobs": []}

        def get_kernelspec(self, kernel_name: str) -> dict:
            assert kernel_name == "python3"
            return {"name": "python3", "spec": {"argv": ["/repo/.venv/bin/python", "-m"]}}

    monkeypatch.setattr(cli_main, "_sdk_control", lambda ctx: FakeControl())

    result = runner.invoke(cli, ["setup", "doctor"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["hypernote_api"] == "ok"
    assert payload["default_kernel"] == "/repo/.venv/bin/python"


def test_setup_serve_launches_jupyterlab_with_hypernote_extensions(
    runner,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(cli_main.importlib.util, "find_spec", lambda name: object())
    captured: dict[str, object] = {}

    def fake_run(cmd, cwd, check):  # noqa: ANN001
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["check"] = check
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli_main.subprocess, "run", fake_run)

    result = runner.invoke(
        cli,
        [
            "--server",
            "http://127.0.0.1:8899",
            "setup",
            "serve",
            "--root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "Starting Hypernote Jupyter server at http://127.0.0.1:8899" in result.output
    cmd = captured["cmd"]
    assert cmd[:3] == [cli_main.sys.executable, "-m", "jupyterlab"]
    assert "--no-browser" in cmd
    assert "--ServerApp.ip=127.0.0.1" in cmd
    assert "--ServerApp.port=8899" in cmd
    assert f"--ServerApp.root_dir={tmp_path.resolve()}" in cmd
    assert (
        f"--ServerApp.jpserver_extensions={cli_main.HYPERNOTE_EXTENSION_FLAGS}" in cmd
    )
    assert captured["cwd"] == str(tmp_path.resolve())
    assert captured["check"] is False


def test_setup_serve_fails_cleanly_without_jupyterlab(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(cli_main.importlib.util, "find_spec", lambda name: None)

    result = runner.invoke(cli, ["setup", "serve", "--root", str(tmp_path)])

    assert result.exit_code != 0
    assert "jupyterlab is not installed" in result.output


def test_setup_doctor_with_path_reports_runtime_and_kernel_details(runner, monkeypatch):
    monkeypatch.setattr(cli_main, "_stdout_is_tty", lambda: False)

    class FakeControl:
        def list_jobs(self) -> dict:
            return {"jobs": []}

        def get_notebook_document(self, notebook_id: str, *, content: bool = True) -> dict:
            assert notebook_id == "demo.ipynb"
            assert content is True
            return {
                "path": notebook_id,
                "content": {
                    "metadata": {
                        "kernelspec": {"display_name": "Subtext", "name": "subtext-kernel"}
                    }
                },
            }

        def get_runtime_status(self, notebook_id: str) -> dict:
            assert notebook_id == "demo.ipynb"
            return {"state": "live-detached", "kernel_name": "python3"}

        def get_kernelspec(self, kernel_name: str) -> dict:
            assert kernel_name == "subtext-kernel"
            return {"name": kernel_name, "spec": {"argv": ["/envs/subtext/bin/python", "-m"]}}

    monkeypatch.setattr(cli_main, "_sdk_control", lambda ctx: FakeControl())

    result = runner.invoke(cli, ["setup", "doctor", "--path", "demo.ipynb"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["path"] == "demo.ipynb"
    assert payload["notebook_kernelspec"] == "subtext-kernel"
    assert payload["runtime_state"] == "live-detached"
    assert payload["runtime_kernel_name"] == "python3"
    assert payload["kernelspec_launcher"] == "/envs/subtext/bin/python"
    assert payload["warnings"]


def test_job_await_timeout_surfaces_recovery_hint(runner, fake_notebooks, monkeypatch):
    monkeypatch.setattr(cli_main, "_stdout_is_tty", lambda: False)
    nb = fake_notebooks.setdefault("demo.ipynb", FakeNotebook("demo.ipynb"))
    nb.cells.insert_code("print(1)", id="code-1")
    job = FakeTimeoutJob(nb, ("code-1",))
    job.status = JobStatus.RUNNING

    class FakeControl:
        def get_job(self, job_id: str) -> FakeJob:
            assert job_id == "job-1"
            return job

    monkeypatch.setattr(cli_main, "_sdk_control", lambda ctx: FakeControl())

    result = runner.invoke(cli, ["job", "await", "job-1", "--timeout", "0.01"])

    assert result.exit_code != 0
    assert "last status: running" in result.output
    assert "hypernote job get job-1" in result.output
    assert "hypernote cat demo.ipynb --no-outputs" in result.output
