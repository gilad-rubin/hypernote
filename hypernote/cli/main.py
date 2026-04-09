"""Agent-first Hypernote CLI backed by the Python SDK."""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, Iterable

import click
import httpx

from hypernote import (
    CellHandle,
    CellNotFoundError,
    CellType,
    ExecutionTimeoutError,
    HypernoteError,
    InputNotExpectedError,
    Job,
    JobStatus,
    Notebook,
    NotebookNotFoundError,
    NotebookStatus,
    Runtime,
    RuntimeUnavailableError,
    connect,
)
from hypernote.sdk import (
    DEFAULT_READ_OUTPUT_CHARS,
    _Config,
    _control_plane,
    _job_timeout_message,
)

TERMINAL_STATUSES = {
    JobStatus.SUCCEEDED,
    JobStatus.FAILED,
    JobStatus.INTERRUPTED,
    JobStatus.AWAITING_INPUT,
}
PROGRESS_CHOICES = click.Choice(["quiet", "events", "full"])
HYPERNOTE_EXTENSION_FLAGS = (
    "{'hypernote': True, 'jupyter_server_nbmodel': True, 'jupyter_server_ydoc': True}"
)
DEFAULT_SOURCE_PREVIEW_CHARS = 120
DEFAULT_OUTPUT_PREVIEW_CHARS = DEFAULT_READ_OUTPUT_CHARS
DEFAULT_TAIL_OUTPUT_CHARS = DEFAULT_READ_OUTPUT_CHARS
HOME_NOTEBOOK_LIMIT = 5


@dataclass(frozen=True)
class CLIConfig:
    server: str
    token: str | None
    actor_id: str
    actor_type: str
    timeout: float


def _cli_errors(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (
            HypernoteError,
            NotebookNotFoundError,
            CellNotFoundError,
            RuntimeUnavailableError,
            ExecutionTimeoutError,
            InputNotExpectedError,
        ) as exc:
            raise click.ClickException(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise click.ClickException(str(exc)) from exc

    return wrapper


def _stdout_is_tty() -> bool:
    return sys.stdout.isatty()


def _compact_json(data: Any) -> str:
    return json.dumps(data, separators=(",", ":"), default=str)


def _pretty_json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


def _echo_json(data: Any, *, pretty: bool = False) -> None:
    click.echo(_pretty_json(data) if pretty else _compact_json(data))


def _append_hints(payload: dict[str, Any], hints: list[str] | None) -> dict[str, Any]:
    if hints:
        payload["hints"] = hints
    return payload


def _ctx_config(ctx: click.Context) -> CLIConfig:
    return ctx.obj["config"]


def _server_host_port(server: str) -> tuple[str, int]:
    parsed = urllib.parse.urlparse(server)
    host = parsed.hostname or "127.0.0.1"
    if parsed.port is not None:
        return host, parsed.port
    if parsed.scheme == "https":
        return host, 443
    return host, 8888


def _require_jupyterlab() -> None:
    if importlib.util.find_spec("jupyterlab") is not None:
        return
    raise click.ClickException(
        "jupyterlab is not installed in this Python environment. "
        "Install it in the target repo env, then rerun `hypernote setup serve`."
    )


def _serve_command(
    *,
    root: Path,
    host: str,
    port: int,
    token: str,
    no_browser: bool,
) -> list[str]:
    cmd = [sys.executable, "-m", "jupyterlab"]
    if no_browser:
        cmd.append("--no-browser")
    cmd.extend(
        [
            f"--ServerApp.ip={host}",
            f"--ServerApp.port={port}",
            "--ServerApp.port_retries=0",
            f"--ServerApp.token={token}",
            "--ServerApp.password=",
            "--ServerApp.disable_check_xsrf=True",
            f"--ServerApp.root_dir={root}",
            f"--ServerApp.jpserver_extensions={HYPERNOTE_EXTENSION_FLAGS}",
        ]
    )
    return cmd


def _sdk_notebook(ctx: click.Context, path: str, *, create: bool = False) -> Notebook:
    cfg = _ctx_config(ctx)
    return connect(
        path,
        create=create,
        server=cfg.server,
        token=cfg.token,
        actor_id=cfg.actor_id,
        actor_type=cfg.actor_type,
        timeout=cfg.timeout,
    )


def _sdk_control(ctx: click.Context):
    cfg = _ctx_config(ctx)
    return _control_plane(
        _Config(
            server=cfg.server,
            token=cfg.token,
            actor_id=cfg.actor_id,
            actor_type=cfg.actor_type,
            timeout=cfg.timeout,
        )
    )


def _final_output_mode(*, json_flag: bool, human_flag: bool) -> str:
    if json_flag and human_flag:
        raise click.ClickException("Choose only one of --json or --human")
    if json_flag:
        return "json"
    if human_flag:
        return "human"
    return "human" if _stdout_is_tty() else "json"


def _run_output_mode(
    *,
    json_flag: bool,
    human_flag: bool,
    watch_flag: bool,
    stream_json_flag: bool,
) -> str:
    if stream_json_flag and (json_flag or human_flag or watch_flag):
        raise click.ClickException(
            "--stream-json cannot be combined with --json, --human, or --watch"
        )
    if watch_flag and json_flag:
        raise click.ClickException("--watch cannot be combined with --json")
    if json_flag and human_flag:
        raise click.ClickException("Choose only one of --json or --human")

    if stream_json_flag:
        return "stream_json"
    if watch_flag:
        return "watch_human"
    if json_flag:
        return "json"
    if human_flag:
        return "human"
    return "watch_human" if _stdout_is_tty() else "json"


def _status_to_dict(status: NotebookStatus) -> dict[str, Any]:
    return status.to_dict()


def _runtime_to_dict(runtime: Runtime) -> dict[str, Any]:
    return runtime.to_dict()


def _job_to_dict(job: Job) -> dict[str, Any]:
    return job.to_dict()


def _snapshot_from_status(status: NotebookStatus) -> str:
    return status.current.token


def _normalize_preview_text(value: str) -> str:
    return " ".join(value.split())


def _truncate_text(
    value: str | None,
    *,
    limit: int,
    full: bool = False,
) -> tuple[str | None, dict[str, Any] | None]:
    if value is None:
        return None, None
    normalized = _normalize_preview_text(value)
    if full or limit <= 0 or len(normalized) <= limit:
        return normalized, None
    truncated = normalized[: max(limit - 1, 0)].rstrip()
    if truncated and len(normalized) > len(truncated):
        truncated = f"{truncated}…"
    return truncated, {"truncated": True, "total_chars": len(normalized)}


def _render_text_preview(
    value: str | None,
    *,
    limit: int,
    full: bool = False,
) -> dict[str, Any]:
    text, truncation = _truncate_text(value, limit=limit, full=full)
    payload: dict[str, Any] = {"text": text}
    if truncation is not None:
        payload.update(truncation)
    return payload


def _output_text(output: dict[str, Any]) -> str:
    if output.get("output_type") == "stream":
        return str(output.get("text", ""))
    if output.get("output_type") == "error":
        traceback = output.get("traceback") or ()
        if traceback:
            return "\n".join(str(line) for line in traceback)
        return f"{output.get('ename', 'Error')}: {output.get('evalue', '')}".strip()
    data = output.get("data")
    if isinstance(data, dict):
        if "text/plain" in data:
            text_value = data["text/plain"]
            if isinstance(text_value, list):
                return "".join(str(part) for part in text_value)
            return str(text_value)
    if "text" in output:
        return str(output["text"])
    return _normalize_preview_text(json.dumps(output, default=str))


def _summarize_output(
    output: dict[str, Any],
    *,
    max_chars: int,
    full_output: bool,
) -> dict[str, Any]:
    summary = {"output_type": output.get("output_type", "unknown")}
    text_payload = _render_text_preview(_output_text(output), limit=max_chars, full=full_output)
    summary["text"] = text_payload["text"]
    if text_payload.get("truncated"):
        summary["truncated"] = True
        summary["total_chars"] = text_payload["total_chars"]
        summary["hint"] = (
            f"truncated, {text_payload['total_chars']} chars total; "
            "use --full-output to see complete text"
        )
    if output.get("output_type") == "error":
        summary["ename"] = output.get("ename")
        summary["evalue"] = output.get("evalue")
    return summary


def _has_error_output(outputs: Iterable[dict[str, Any]]) -> bool:
    return any(output.get("output_type") == "error" for output in outputs)


def _cell_output_count(outputs: Iterable[dict[str, Any]]) -> int:
    return sum(1 for _ in outputs)


def _cell_source_preview(source: str | None, *, full: bool = False) -> dict[str, Any]:
    return _render_text_preview(source, limit=DEFAULT_SOURCE_PREVIEW_CHARS, full=full)


def _cell_output_preview(
    outputs: Iterable[dict[str, Any]],
    *,
    max_chars: int,
    full_output: bool,
) -> dict[str, Any] | None:
    outputs = list(outputs)
    if not outputs:
        return None
    return _summarize_output(outputs[-1], max_chars=max_chars, full_output=full_output)


def _matches_query(*, query: str | None, text_parts: Iterable[str | None]) -> bool:
    if not query:
        return True
    query_lower = query.lower()
    return any(query_lower in (part or "").lower() for part in text_parts)


def _status_aggregates(status: NotebookStatus) -> dict[str, Any]:
    code_cells = 0
    markdown_cells = 0
    raw_cells = 0
    executed_cells = 0
    failed_cells = 0
    changed_cells = 0
    output_cells = 0

    for cell in status.cells:
        if cell.type == CellType.CODE:
            code_cells += 1
        elif cell.type == CellType.MARKDOWN:
            markdown_cells += 1
        else:
            raw_cells += 1
        if cell.execution_count is not None:
            executed_cells += 1
        outputs = list(cell.outputs or ())
        if outputs:
            output_cells += 1
        if _has_error_output(outputs):
            failed_cells += 1
        if cell.change_kinds:
            changed_cells += 1

    return {
        "cells_total": len(status.cells),
        "code_cells": code_cells,
        "markdown_cells": markdown_cells,
        "raw_cells": raw_cells,
        "executed_cells": executed_cells,
        "failed_cells": failed_cells,
        "changed_cells": changed_cells,
        "output_cells": output_cells,
        "runtime_state": status.runtime.value,
        "snapshot": _snapshot_from_status(status),
        "summary": status.summary,
    }


def _compact_status_cells(
    status: NotebookStatus,
    *,
    full: bool,
    full_output: bool,
    failed_only: bool,
    query: str | None,
    max_output_chars: int,
) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for cell in status.cells:
        outputs = list(cell.outputs or ())
        source_preview = _cell_source_preview(cell.source, full=full)
        output_preview = _cell_output_preview(
            outputs,
            max_chars=max_output_chars,
            full_output=full_output,
        )
        has_error = _has_error_output(outputs)
        if failed_only and not has_error:
            continue
        if not _matches_query(
            query=query,
            text_parts=[
                cell.source,
                output_preview["text"] if output_preview else None,
                cell.id,
            ],
        ):
            continue
        entry: dict[str, Any] = {
            "id": cell.id,
            "type": cell.type.value,
            "execution_count": cell.execution_count,
            "output_count": len(outputs),
            "has_error_output": has_error,
            "source_preview": source_preview["text"],
        }
        if source_preview.get("truncated"):
            entry["source_truncated"] = True
            entry["source_total_chars"] = source_preview["total_chars"]
            entry["source_hint"] = "truncated, use --full to see complete source"
        if output_preview is not None:
            entry["output_preview"] = output_preview["text"]
            if output_preview.get("truncated"):
                entry["output_truncated"] = True
                entry["output_total_chars"] = output_preview["total_chars"]
                entry["output_hint"] = (
                    "truncated, use --full-output to see complete output text"
                )
        if cell.change_kinds:
            entry["change_kinds"] = [kind.value for kind in cell.change_kinds]
        cells.append(entry)
    return cells


def _build_status_payload(
    status: NotebookStatus,
    *,
    path: str,
    full: bool,
    full_output: bool,
    failed_only: bool,
    query: str | None,
    max_output_chars: int,
) -> dict[str, Any]:
    return {
        "command": "status",
        "path": path,
        **status.compact_dict(
            full_source=full,
            include_outputs=full,
            full_output=full_output,
            failed_only=failed_only,
            query=query,
            max_output_chars=max_output_chars,
            include_details=full,
        ),
    }


def _cell_payload_for_cat(
    cell: Any,
    *,
    include_outputs: bool,
    full_source: bool,
    full_output: bool,
    max_output_chars: int,
) -> dict[str, Any]:
    outputs = list(cell.outputs or ())
    source_preview = _cell_source_preview(cell.source, full=full_source)
    entry: dict[str, Any] = {
        "id": cell.id,
        "type": cell.type.value,
        "execution_count": cell.execution_count,
        "output_count": len(outputs),
        "has_error_output": _has_error_output(outputs),
        "source_preview": source_preview["text"],
    }
    if source_preview.get("truncated"):
        entry["source_truncated"] = True
        entry["source_total_chars"] = source_preview["total_chars"]
    if full_source:
        entry["source"] = cell.source
    preview = _cell_output_preview(outputs, max_chars=max_output_chars, full_output=full_output)
    if preview is not None:
        entry["output_preview"] = preview["text"]
        if preview.get("truncated"):
            entry["output_truncated"] = True
            entry["output_total_chars"] = preview["total_chars"]
    if include_outputs:
        entry["outputs"] = [
            _summarize_output(output, max_chars=max_output_chars, full_output=full_output)
            for output in outputs
        ]
    return entry


def _cat_cells(
    notebook: Notebook,
    *,
    include_outputs: bool,
    full_source: bool,
    full_output: bool,
    max_output_chars: int,
) -> list[dict[str, Any]]:
    status = notebook.status(full=True)
    return [
        _cell_payload_for_cat(
            cell,
            include_outputs=include_outputs,
            full_source=full_source,
            full_output=full_output,
            max_output_chars=max_output_chars,
        )
        for cell in status.cells
    ]


def _cat_output_payload(
    status: NotebookStatus,
    *,
    cell_id: str,
    tail: bool,
    max_output_chars: int,
    full_output: bool,
) -> dict[str, Any]:
    payload = status.cell(cell_id).output_payload(
        max_chars=max_output_chars,
        full_output=full_output,
        tail=tail,
    )
    payload["selected_cell_id"] = cell_id
    return payload


def _build_cat_payload(
    notebook: Notebook,
    *,
    include_outputs: bool,
    full_source: bool,
    full_output: bool,
    max_output_chars: int,
    cell_id: str | None,
    output_cell_id: str | None,
    tail_output_cell_id: str | None,
) -> dict[str, Any]:
    status = notebook.status(full=True)
    if output_cell_id:
        payload = _cat_output_payload(
            status,
            cell_id=output_cell_id,
            tail=False,
            max_output_chars=max_output_chars,
            full_output=full_output,
        )
        payload["summary"] = status.aggregates()["summary"]
        return payload
    if tail_output_cell_id:
        payload = _cat_output_payload(
            status,
            cell_id=tail_output_cell_id,
            tail=True,
            max_output_chars=max_output_chars,
            full_output=full_output,
        )
        payload["summary"] = status.aggregates()["summary"]
        return payload

    if cell_id:
        cells = [
            status.cell(cell_id).compact_dict(
                full_source=full_source,
                include_outputs=include_outputs,
                full_output=full_output,
                max_output_chars=max_output_chars,
            )
        ]
    else:
        cells = status.compact_cells(
            full_source=full_source,
            include_outputs=include_outputs,
            full_output=full_output,
            max_output_chars=max_output_chars,
        )

    failed_cells = sum(1 for entry in cells if entry.get("has_error_output"))
    cells_with_outputs = sum(1 for entry in cells if entry.get("output_count", 0) > 0)
    return {
        "command": "cat",
        "path": notebook.path,
        "summary": status.aggregates()["summary"],
        "cells_total": len(cells),
        "failed_cells": failed_cells,
        "cells_with_outputs": cells_with_outputs,
        "cell_id": cell_id,
        "selected_cell_id": cell_id,
        "cells": cells,
    }


def _hinted_result(data: dict[str, Any], *hints: str | None) -> dict[str, Any]:
    filtered = [hint for hint in hints if hint]
    if filtered:
        data["hints"] = filtered
    return data


def _home_notebooks() -> list[Path]:
    candidates = list(Path.cwd().glob("*.ipynb"))
    tmp_dir = Path.cwd() / "tmp"
    if tmp_dir.exists():
        candidates.extend(tmp_dir.glob("*.ipynb"))
    unique: dict[str, Path] = {}
    for path in candidates:
        unique.setdefault(str(path.relative_to(Path.cwd())), path)
    return [unique[key] for key in sorted(unique)]


def _build_home_payload(ctx: click.Context) -> dict[str, Any]:
    cfg = _ctx_config(ctx)
    payload: dict[str, Any] = {
        "command": "home",
        "bin": Path(sys.argv[0]).name,
        "server": cfg.server,
        "cwd": str(Path.cwd()),
    }

    control = _sdk_control(ctx)
    jobs: list[dict[str, Any]] = []
    server_reachable = False
    server_error: str | None = None
    try:
        jobs_payload = control.list_jobs()
        server_reachable = True
        jobs = list(jobs_payload.get("jobs", []))
    except Exception as exc:  # pragma: no cover - exercised via CLI output
        server_error = str(exc)

    notebooks = _home_notebooks()
    payload["server_reachable"] = server_reachable
    payload["jobs_count"] = len(jobs)
    payload["notebooks_count"] = len(notebooks)
    payload["notebooks"] = [str(path.relative_to(Path.cwd())) for path in notebooks[:5]]
    if server_error is not None:
        payload["server_error"] = server_error

    first_notebook = payload["notebooks"][0] if payload["notebooks"] else None
    hints = [
        f"Run `hypernote status {first_notebook}` to inspect notebook state"
        if first_notebook
        else "Run `hypernote create tmp/demo.ipynb --empty` to start a new notebook",
        f"Run `hypernote ix {first_notebook} -s 'print(42)'` to insert and run a code cell"
        if first_notebook
        else "Run `hypernote ix tmp/demo.ipynb -s 'print(42)'` after creating a notebook",
        "Run `hypernote setup doctor` to verify server and kernelspec health",
    ]
    return _hinted_result(payload, *hints)


def _human_status(status: NotebookStatus, *, full: bool = False) -> str:
    lines = [_status_summary(status).get("headline", status.summary)]
    if full:
        for cell in status.cells:
            change_suffix = ""
            if cell.change_kinds:
                kinds = ", ".join(kind.value for kind in cell.change_kinds)
                change_suffix = f" [{kinds}]"
            lines.append(
                f"- {cell.id} ({cell.type.value}) exec={cell.execution_count}{change_suffix}"
            )
            if cell.source:
                lines.append(f"  {cell.source}")
    return "\n".join(lines)


def _human_diff(status: NotebookStatus, *, full: bool = False) -> str:
    lines = [status.summary]
    for cell in status.cells:
        kinds = ", ".join(kind.value for kind in cell.change_kinds) or "unchanged"
        lines.append(f"- {cell.id}: {kinds}")
        if full and cell.source:
            lines.append(f"  {cell.source}")
    return "\n".join(lines)


def _human_status_payload(data: dict[str, Any]) -> str:
    lines = [
        (
            f"{Path(data['path']).name} · {data['cells_total']} cells "
            f"({data['code_cells']} code, {data['markdown_cells']} markdown) · "
            f"{data['executed_cells']} executed · {data['failed_cells']} failed · "
            f"runtime {data['runtime_state']} · snapshot {data['snapshot']}"
        )
    ]
    if not data["cells"]:
        lines.append("- 0 matching cells")
    else:
        for cell in data["cells"]:
            lines.append(
                f"- {cell['id']} ({cell['type']}) exec={cell.get('execution_count')} "
                f"outputs={cell.get('output_count', 0)}"
            )
            if cell.get("source_preview"):
                lines.append(f"  {cell['source_preview']}")
            if cell.get("output_preview"):
                lines.append(f"  out: {cell['output_preview']}")
    return _with_hints_text("\n".join(lines), data.get("hints"))


def _human_cat(data: dict[str, Any]) -> str:
    if "outputs" in data and "cells" not in data:
        lines = [
            (
                f"{Path(data['path']).name} · {data.get('cell_id')} · "
                f"{data.get('output_count', 0)} outputs"
            )
        ]
        if data.get("tail_output"):
            lines.append(f"tail: {data['tail_output']}")
        for output in data.get("outputs", []):
            text = output.get("text")
            if text:
                lines.append(f"- {output.get('output_type', 'unknown')}: {text}")
        return _with_hints_text("\n".join(lines), data.get("hints"))

    cells = data.get("cells", [])
    summary = data.get("summary", {})
    lines = [summary.get("headline", f"{Path(data['path']).name} · {len(cells)} cells")]
    if not cells:
        lines.append("- 0 matching cells")
    for cell in cells:
        outputs = cell.get("outputs", [])
        lines.append(f"- {cell['id']} ({cell['type']})")
        lines.append(
            f"  exec={cell.get('execution_count')} "
            f"outputs={cell.get('output_count', len(outputs))}"
        )
        if "source_preview" in cell:
            lines.append(f"  {cell['source_preview']}")
            if cell.get("source_truncated"):
                lines.append(
                    "  "
                    f"truncated, {cell.get('source_total_chars')} chars total; "
                    "use --full to see complete source"
                )
        elif "source" in cell:
            lines.append(f"  {cell['source']}")
        if cell.get("output_preview"):
            lines.append(f"  out: {cell['output_preview']}")
            if cell.get("output_truncated"):
                lines.append(
                    "  "
                    f"truncated, {cell.get('output_total_chars')} chars total; "
                    "use --full-output to see complete text"
                )
        for output in outputs:
            text = output.get("text")
            if text:
                lines.append(f"  output[{output.get('output_type', 'unknown')}]: {text}")
            if output.get("hint"):
                lines.append(f"  {output['hint']}")
    return _with_hints_text("\n".join(lines), data.get("hints"))


def _with_hints_text(body: str, hints: Iterable[str] | None) -> str:
    lines = [body] if body else []
    for hint in hints or ():
        lines.append(f"hint: {hint}")
    return "\n".join(lines)


def _preview_text(
    value: str,
    *,
    limit: int,
    escape_hatch: str,
) -> dict[str, Any]:
    text = str(value)
    if limit <= 0 or len(text) <= limit:
        return {"text": text, "truncated": False, "total_chars": len(text)}
    return {
        "text": text[: limit - 1] + "…",
        "truncated": True,
        "total_chars": len(text),
        "hint": f"truncated, {len(text)} chars total; use {escape_hatch} to see complete text",
    }


def _output_text(output: dict[str, Any]) -> str | None:
    output_type = output.get("output_type")
    if output_type == "stream":
        return str(output.get("text", ""))
    if output_type == "error":
        traceback = output.get("traceback") or ()
        if traceback:
            return "\n".join(str(line) for line in traceback)
        ename = output.get("ename")
        evalue = output.get("evalue")
        if ename or evalue:
            return ": ".join(part for part in [str(ename or ""), str(evalue or "")] if part)
        return None
    if output_type in {"display_data", "execute_result"}:
        data = output.get("data") or {}
        if "text/plain" in data:
            return str(data["text/plain"])
    return None


def _output_payload(
    output: dict[str, Any],
    *,
    full_output: bool,
    max_output_chars: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"output_type": output.get("output_type")}
    if "name" in output:
        payload["name"] = output.get("name")
    if "ename" in output:
        payload["ename"] = output.get("ename")
    if "evalue" in output:
        payload["evalue"] = output.get("evalue")

    text = _output_text(output)
    if text is not None:
        preview = (
            {"text": text, "truncated": False, "total_chars": len(text)}
            if full_output
            else _preview_text(text, limit=max_output_chars, escape_hatch="--full-output")
        )
        payload["text"] = preview["text"]
        payload["truncated"] = preview["truncated"]
        payload["total_chars"] = preview["total_chars"]
        if preview.get("hint"):
            payload["hint"] = preview["hint"]
    return payload


def _cell_has_error(outputs: Iterable[dict[str, Any]] | None) -> bool:
    return any(output.get("output_type") == "error" for output in (outputs or ()))


def _cell_payload_summary(
    cell,
    *,
    full_source: bool,
    include_outputs: bool,
    full_output: bool,
    max_output_chars: int,
) -> dict[str, Any]:
    outputs = tuple(cell.outputs or ())
    payload: dict[str, Any] = {
        "id": cell.id,
        "type": cell.type.value,
        "execution_count": cell.execution_count,
        "output_count": len(outputs),
        "has_error": _cell_has_error(outputs),
    }

    source_preview = _preview_text(
        cell.source,
        limit=DEFAULT_SOURCE_PREVIEW_CHARS,
        escape_hatch="--full",
    )
    if full_source:
        payload["source"] = cell.source
    else:
        payload["source_preview"] = source_preview["text"]
        payload["source_truncated"] = source_preview["truncated"]
        payload["source_chars"] = source_preview["total_chars"]
        if source_preview.get("hint"):
            payload["source_hint"] = source_preview["hint"]

    if include_outputs:
        payload["outputs"] = [
            _output_payload(output, full_output=full_output, max_output_chars=max_output_chars)
            for output in outputs
        ]
    return payload


def _job_hint(command: str) -> str:
    return f"Run `{command}`"


def _dedupe_hints(hints: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for hint in hints:
        if not hint or hint in seen:
            continue
        seen.add(hint)
        ordered.append(hint)
    return ordered


def _status_summary(status: NotebookStatus, *, runtime_state: str | None = None) -> dict[str, Any]:
    cells = tuple(status.cells)
    code_cells = sum(1 for cell in cells if cell.type == CellType.CODE)
    markdown_cells = sum(1 for cell in cells if cell.type == CellType.MARKDOWN)
    executed_cells = sum(1 for cell in cells if cell.execution_count is not None)
    failed_cells = sum(1 for cell in cells if _cell_has_error(cell.outputs))
    output_cells = sum(1 for cell in cells if cell.outputs)
    output_count = sum(len(cell.outputs or ()) for cell in cells)
    runtime_value = runtime_state or getattr(getattr(status, "runtime", None), "value", None)
    headline_parts = [
        f"{Path(status.notebook_path).name}",
        f"{len(cells)} cells ({code_cells} code, {markdown_cells} markdown)",
        f"{executed_cells} executed",
        f"{failed_cells} failed",
        f"{output_count} outputs",
    ]
    if runtime_value:
        headline_parts.append(f"runtime {runtime_value}")
    return {
        "headline": " · ".join(headline_parts),
        "cell_count": len(cells),
        "code_cells": code_cells,
        "markdown_cells": markdown_cells,
        "executed_cells": executed_cells,
        "failed_cells": failed_cells,
        "cells_with_output": output_cells,
        "output_count": output_count,
        "runtime": runtime_value,
    }


def _status_hints(path: str, *, snapshot: str | None = None) -> list[str]:
    hints = [
        _job_hint(f"hypernote cat {shlex.quote(path)}"),
        _job_hint(f"hypernote ix {shlex.quote(path)} -s 'print(42)'"),
    ]
    if snapshot:
        hints.insert(
            1,
            _job_hint(f"hypernote diff {shlex.quote(path)} --snapshot {shlex.quote(snapshot)}"),
        )
    return _dedupe_hints(hints)


def _cat_hints(path: str) -> list[str]:
    return _dedupe_hints(
        [
            _job_hint(f"hypernote status {shlex.quote(path)}"),
            _job_hint(f"hypernote ix {shlex.quote(path)} -s 'print(42)'"),
        ]
    )


def _cell_matches_query(cell, query: str | None) -> bool:
    if not query:
        return True
    needle = query.lower()
    if needle in str(cell.id).lower() or needle in str(cell.source).lower():
        return True
    for output in cell.outputs or ():
        text = _output_text(output)
        if text and needle in text.lower():
            return True
    return False


def _status_payload(
    status: NotebookStatus,
    *,
    command: str,
    path: str,
    include_outputs: bool,
    include_full_source: bool,
    max_output_chars: int,
    full_output: bool,
    failed_only: bool,
    query: str | None,
    runtime_state: str | None = None,
) -> dict[str, Any]:
    filtered_cells = []
    for cell in status.cells:
        if failed_only and not _cell_has_error(cell.outputs):
            continue
        if not _cell_matches_query(cell, query):
            continue
        filtered_cells.append(
            _cell_payload_summary(
                cell,
                full_source=include_full_source,
                include_outputs=include_outputs,
                full_output=full_output,
                max_output_chars=max_output_chars,
            )
        )

    payload: dict[str, Any] = {
        "command": command,
        "path": path,
        "snapshot": _snapshot_from_status(status),
        "summary": _status_summary(status, runtime_state=runtime_state),
        "cells": filtered_cells,
        "filters": {
            "failed_only": failed_only,
            "query": query,
        },
    }
    if not filtered_cells:
        payload["empty"] = True
    return payload


def _human_status_payload(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [summary.get("headline", payload.get("path", "status"))]
    if payload.get("snapshot"):
        lines.append(f"snapshot: {payload['snapshot']}")
    if payload.get("empty"):
        lines.append("0 matching cells")
    for cell in payload.get("cells", []):
        lines.append(
            f"- {cell['id']} ({cell['type']}) "
            f"exec={cell.get('execution_count')} "
            f"outputs={cell.get('output_count', 0)}"
        )
        if "source_preview" in cell:
            lines.append(f"  {cell['source_preview']}")
            if cell.get("source_truncated"):
                lines.append(
                    "  "
                    f"truncated, {cell.get('source_total_chars')} chars total; "
                    "use --full to see complete source"
                )
        elif "source" in cell:
            lines.append(f"  {cell['source']}")
        if cell.get("output_preview"):
            lines.append(f"  out: {cell['output_preview']}")
            if cell.get("output_truncated"):
                lines.append(
                    "  "
                    f"truncated, {cell.get('output_total_chars')} chars total; "
                    "use --full-output to see complete text"
                )
    return _with_hints_text("\n".join(lines), payload.get("hints"))


def _job_result_hints(
    *,
    path: str,
    job: Job,
    inserted_cells: Iterable[CellHandle],
) -> list[str]:
    inserted_ids = [cell.id for cell in inserted_cells]
    first_cell_id = next(iter(inserted_ids or list(job.cell_ids or ())), None)
    if job.status == JobStatus.SUCCEEDED:
        return _dedupe_hints(
            [
                _job_hint(
                    f"hypernote cat {shlex.quote(path)}"
                    + (
                        f" --cell {shlex.quote(first_cell_id)}"
                        if first_cell_id is not None
                        else ""
                    )
                ),
                _job_hint(f"hypernote status {shlex.quote(path)}"),
                _job_hint(f"hypernote ix {shlex.quote(path)} -s 'print(42)'"),
            ]
        )
    if job.status == JobStatus.FAILED and first_cell_id is not None:
        return _dedupe_hints(
            [
                _job_hint(
                    f"hypernote cat {shlex.quote(path)} --output {shlex.quote(first_cell_id)}"
                ),
                _job_hint(
                    "hypernote edit replace "
                    f"{shlex.quote(path)} {shlex.quote(first_cell_id)} -s '...'"
                ),
                _job_hint(f"hypernote exec {shlex.quote(path)} {shlex.quote(first_cell_id)}"),
            ]
        )
    if job.status == JobStatus.AWAITING_INPUT:
        return _dedupe_hints(
            [
                _job_hint(f"hypernote job stdin {shlex.quote(job.id)} --value '...'"),
                _job_hint(f"hypernote job await {shlex.quote(job.id)}"),
            ]
        )
    return _dedupe_hints(
        [
            _job_hint(f"hypernote job get {shlex.quote(job.id)}"),
            _job_hint(f"hypernote job await {shlex.quote(job.id)}"),
        ]
    )


def _local_notebook_paths(root: Path, *, limit: int = HOME_NOTEBOOK_LIMIT) -> tuple[int, list[str]]:
    try:
        notebook_paths = sorted(
            {
                path.relative_to(root).as_posix()
                for path in root.rglob("*.ipynb")
                if ".ipynb_checkpoints" not in path.parts
            }
        )
    except OSError:
        return 0, []
    return len(notebook_paths), notebook_paths[:limit]


def _home_payload(ctx: click.Context) -> dict[str, Any]:
    cfg = _ctx_config(ctx)
    payload: dict[str, Any] = {
        "command": "home",
        "bin": "hypernote",
        "description": "Browse and manage Hypernote notebooks for the current workspace",
        "server": cfg.server,
        "server_reachable": False,
    }
    try:
        jobs_payload = _sdk_control(ctx).list_jobs()
    except Exception as exc:  # pragma: no cover - networked failure surface
        payload["server_error"] = str(exc)
        payload["active_jobs"] = []
        payload["active_job_count"] = 0
    else:
        active_jobs: list[dict[str, Any]] = []
        payload["server_reachable"] = True
        for job in jobs_payload.get("jobs", []):
            if job.get("status") in {"succeeded", "failed", "interrupted"}:
                continue
            try:
                target_cells = json.loads(job.get("target_cells") or "[]")
            except json.JSONDecodeError:
                target_cells = []
            active_jobs.append(
                {
                    "job_id": job.get("job_id"),
                    "status": job.get("status"),
                    "path": job.get("notebook_id"),
                    "target_cell_count": len(target_cells),
                }
            )
        payload["active_jobs"] = active_jobs
        payload["active_job_count"] = len(active_jobs)

    notebook_count, notebook_paths = _local_notebook_paths(Path.cwd())
    payload["workspace_notebook_count"] = notebook_count
    payload["workspace_notebooks"] = notebook_paths
    first_notebook = notebook_paths[0] if notebook_paths else None
    payload["hints"] = _dedupe_hints(
        [
            _job_hint("hypernote setup serve") if not payload["server_reachable"] else None,
            _job_hint(f"hypernote status {shlex.quote(first_notebook)}")
            if first_notebook
            else _job_hint("hypernote create tmp/demo.ipynb --empty"),
            _job_hint(f"hypernote ix {shlex.quote(first_notebook)} -s 'print(42)'")
            if first_notebook
            else _job_hint("hypernote ix tmp/demo.ipynb -s 'print(42)'"),
            _job_hint("hypernote setup doctor"),
        ]
    )
    return payload


def _human_home(data: dict[str, Any]) -> str:
    lines = [
        f"bin: {data['bin']}",
        f"description: {data['description']}",
        (
            f"server: {data['server']} "
            f"({'reachable' if data.get('server_reachable') else 'unreachable'})"
        ),
        f"active jobs: {data.get('active_job_count', 0)}",
        f"workspace notebooks: {data.get('workspace_notebook_count', 0)}",
    ]
    for notebook_path in data.get("workspace_notebooks", []):
        lines.append(f"  {notebook_path}")
    for job in data.get("active_jobs", []):
        lines.append(f"  {job['job_id']} · {job['status']} · {job['path']}")
    if data.get("server_error"):
        lines.append(f"server error: {data['server_error']}")
    return _with_hints_text("\n".join(lines), data.get("hints"))


def _extract_outputs(cell: CellHandle) -> tuple[dict[str, Any], ...]:
    return cell.outputs


def _output_deltas(
    previous: tuple[dict[str, Any], ...],
    current: tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    deltas: list[dict[str, Any]] = []
    overlap = min(len(previous), len(current))
    for index in range(overlap):
        prev = previous[index]
        curr = current[index]
        if (
            prev.get("output_type") == "stream"
            and curr.get("output_type") == "stream"
            and prev.get("name") == curr.get("name")
        ):
            prev_text = str(prev.get("text", ""))
            curr_text = str(curr.get("text", ""))
            if curr_text.startswith(prev_text) and curr_text != prev_text:
                deltas.append(
                    {
                        "output_type": "stream",
                        "name": curr.get("name"),
                        "text": curr_text[len(prev_text) :],
                    }
                )
            elif curr != prev:
                deltas.append(curr)
        elif curr != prev:
            deltas.append(curr)
    for output in current[overlap:]:
        deltas.append(output)
    return deltas


def _event(
    event_type: str,
    *,
    job: Job | None = None,
    cell_id: str | None = None,
    status: str | None = None,
    text: str | None = None,
    output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"event": event_type}
    if job is not None:
        payload["job_id"] = job.id
        payload["notebook_path"] = job.notebook_path
    if cell_id is not None:
        payload["cell_id"] = cell_id
    if status is not None:
        payload["status"] = status
    if text is not None:
        payload["text"] = text
    if output is not None:
        payload["output"] = output
    return payload


def _halt_reason(status: JobStatus | None) -> str | None:
    if status == JobStatus.FAILED:
        return "job_failed"
    if status == JobStatus.INTERRUPTED:
        return "job_interrupted"
    if status == JobStatus.AWAITING_INPUT:
        return "awaiting_input"
    return None


def _kernelspec_name_from_document(model: dict[str, Any]) -> str:
    content = model.get("content", {})
    metadata = content.get("metadata", {})
    kernelspec = metadata.get("kernelspec", {})
    return str(kernelspec.get("name") or "python3")


def _kernelspec_launcher(kernelspec: dict[str, Any]) -> str | None:
    spec = kernelspec.get("spec", {})
    argv = spec.get("argv", [])
    if not argv:
        return None
    return str(argv[0])


def _emit_stream_json(payload: dict[str, Any]) -> None:
    click.echo(_compact_json(payload))


def _emit_human_event(payload: dict[str, Any], *, progress: str) -> None:
    event = payload["event"]
    if progress == "quiet" and event not in {"job_completed", "job_failed", "awaiting_input"}:
        return
    if event == "cell_inserted":
        click.echo(f"Inserted {payload['cell_id']}")
    elif event == "job_started":
        click.echo(f"Started job {payload['job_id']}")
    elif event == "cell_started" and progress == "full":
        click.echo(f"Running {payload['cell_id']}")
    elif event == "output_delta":
        text = payload.get("text")
        if text:
            click.echo(text, nl=False)
        elif payload.get("output"):
            click.echo(_compact_json(payload["output"]))
    elif event == "awaiting_input":
        click.echo(f"Job {payload['job_id']} awaiting input")
    elif event == "cell_completed" and progress == "full":
        click.echo(f"Completed {payload['cell_id']}")
    elif event == "job_completed":
        click.echo(f"Job {payload['job_id']} {payload['status']}")
    elif event == "job_failed":
        click.echo(f"Job {payload['job_id']} {payload['status']}")


def _watch_job(
    notebook: Notebook,
    job: Job,
    *,
    mode: str,
    progress: str,
    inserted_cell_ids: Iterable[str] = (),
    timeout: float | None = None,
) -> Job:
    inserted = tuple(inserted_cell_ids)
    cell_ids = tuple(job.cell_ids or inserted)
    if mode == "stream_json":
        for cell_id in inserted:
            _emit_stream_json(_event("cell_inserted", job=job, cell_id=cell_id))
        if progress != "quiet":
            _emit_stream_json(_event("job_started", job=job, status=job.status.value))
    else:
        for cell_id in inserted:
            _emit_human_event(
                _event("cell_inserted", job=job, cell_id=cell_id),
                progress=progress,
            )
        _emit_human_event(
            _event("job_started", job=job, status=job.status.value),
            progress=progress,
        )

    deadline = None if timeout is None else time.monotonic() + timeout
    seen_outputs = {cell_id: tuple() for cell_id in cell_ids}
    started_cells: set[str] = set()

    while True:
        job.refresh()
        for cell_id in cell_ids:
            try:
                cell = notebook.cells[cell_id]
            except CellNotFoundError:
                continue
            outputs = _extract_outputs(cell)
            if cell_id not in started_cells and (
                outputs or cell.execution_count is not None or job.status == JobStatus.RUNNING
            ):
                started_cells.add(cell_id)
                payload = _event("cell_started", job=job, cell_id=cell_id)
                if mode == "stream_json":
                    if progress != "quiet":
                        _emit_stream_json(payload)
                else:
                    _emit_human_event(payload, progress=progress)

            deltas = _output_deltas(seen_outputs.get(cell_id, tuple()), outputs)
            for delta in deltas:
                if delta.get("output_type") == "stream":
                    payload = _event(
                        "output_delta",
                        job=job,
                        cell_id=cell_id,
                        text=str(delta.get("text", "")),
                    )
                else:
                    payload = _event("output_delta", job=job, cell_id=cell_id, output=delta)
                if mode == "stream_json":
                    if progress != "quiet":
                        _emit_stream_json(payload)
                else:
                    _emit_human_event(payload, progress=progress)
            seen_outputs[cell_id] = outputs

        if job.status == JobStatus.AWAITING_INPUT:
            payload = _event("awaiting_input", job=job, status=job.status.value)
            if mode == "stream_json":
                _emit_stream_json(payload)
            else:
                _emit_human_event(payload, progress=progress)
            return job

        if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.INTERRUPTED}:
            if progress == "full":
                for cell_id in cell_ids:
                    payload = _event("cell_completed", job=job, cell_id=cell_id)
                    if mode == "stream_json":
                        _emit_stream_json(payload)
                    else:
                        _emit_human_event(payload, progress=progress)
            event_name = "job_completed" if job.status == JobStatus.SUCCEEDED else "job_failed"
            payload = _event(event_name, job=job, status=job.status.value)
            if mode == "stream_json":
                _emit_stream_json(payload)
            else:
                _emit_human_event(payload, progress=progress)
            return job

        if deadline is not None and time.monotonic() >= deadline:
            raise ExecutionTimeoutError(_job_timeout_message(job))
        time.sleep(0.25)


def _render_result(data: dict[str, Any], *, mode: str, human_renderer) -> None:
    if mode == "json":
        _echo_json(data)
    elif mode == "human":
        click.echo(human_renderer())
    else:  # pretty json helper path
        _echo_json(data, pretty=True)


def _read_text(source: str | None, source_file: str | None) -> str:
    if source is not None and source_file is not None:
        raise click.ClickException("Choose only one of --source or --source-file")
    if source_file is not None:
        return Path(source_file).read_text()
    if source is not None:
        return source
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise click.ClickException("Provide --source, --source-file, or pipe content on stdin")


def _read_cells_payload(
    *,
    source: str | None,
    source_file: str | None,
    cells_json: str | None,
    cells_file: str | None,
    default_cell_type: str = "code",
) -> list[dict[str, Any]]:
    if (source is not None or source_file is not None) and (cells_json or cells_file):
        raise click.ClickException(
            "Choose either --source/--source-file or --cells-json/--cells-file"
        )

    if cells_json or cells_file:
        raw = Path(cells_file).read_text() if cells_file else cells_json
        payload = json.loads(raw or "[]")
        if not isinstance(payload, list):
            raise click.ClickException("Cells payload must be a JSON array")
        cells: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                raise click.ClickException("Each cell payload must be an object")
            source_text = str(item.get("source", ""))
            cell_type = str(item.get("type") or item.get("cell_type") or default_cell_type)
            if cell_type not in {"code", "markdown"}:
                raise click.ClickException(f"Unsupported cell type: {cell_type}")
            cells.append(
                {
                    "cell_type": cell_type,
                    "source": source_text,
                    "id": item.get("id"),
                }
            )
        return cells

    return [{"cell_type": default_cell_type, "source": _read_text(source, source_file)}]


def _insert_cell(
    notebook: Notebook,
    cell_spec: dict[str, Any],
    *,
    before: str | None = None,
    after: str | None = None,
) -> CellHandle:
    cell_type = cell_spec["cell_type"]
    source = cell_spec["source"]
    cell_id = cell_spec.get("id")
    if cell_type == "markdown":
        return notebook.cells.insert_markdown(source, id=cell_id, before=before, after=after)
    return notebook.cells.insert_code(source, id=cell_id, before=before, after=after)


def _cell_payload(cell: CellHandle) -> dict[str, Any]:
    return cell.to_dict()


def _job_result(
    command: str,
    path: str,
    job: Job,
    *,
    inserted_cells: list[CellHandle],
) -> dict[str, Any]:
    return _append_hints(
        {
        "command": command,
        "path": path,
        "job": job.to_dict(),
        "inserted_cells": [_cell_payload(cell) for cell in inserted_cells],
        }
        ,
        _job_result_hints(path=path, job=job, inserted_cells=inserted_cells),
    )


def _cat_payload(
    notebook: Notebook,
    *,
    include_outputs: bool,
    full: bool,
    cell_id: str | None,
    full_output: bool,
    max_output_chars: int,
) -> dict[str, Any]:
    status = notebook.status(full=True)
    cells = [
        _cell_payload_summary(
            cell,
            full_source=full,
            include_outputs=include_outputs,
            full_output=full_output,
            max_output_chars=max_output_chars,
        )
        for cell in status.cells
        if cell_id is None or cell.id == cell_id
    ]
    payload = {
        "command": "cat",
        "path": notebook.path,
        "summary": _status_summary(status),
        "cells": cells,
    }
    if cell_id is not None:
        payload["cell_id"] = cell_id
    return _append_hints(payload, _cat_hints(notebook.path))


@click.group(invoke_without_command=True)
@click.option(
    "--server",
    default=lambda: os.environ.get("HYPERNOTE_SERVER", "http://127.0.0.1:8888"),
    show_default="env HYPERNOTE_SERVER or http://127.0.0.1:8888",
    help="Jupyter server URL",
)
@click.option(
    "--token",
    default=lambda: os.environ.get("HYPERNOTE_TOKEN"),
    help="Jupyter authentication token",
)
@click.option("--actor-id", default="cli-agent", show_default=True, help="Actor identity")
@click.option(
    "--actor-type",
    default="agent",
    show_default=True,
    type=click.Choice(["human", "agent"]),
)
@click.option("--timeout", default=30.0, show_default=True, type=float)
@click.pass_context
def cli(
    ctx: click.Context,
    server: str,
    token: str | None,
    actor_id: str,
    actor_type: str,
    timeout: float,
    ) -> None:
    """Hypernote agent-first notebook CLI."""
    ctx.ensure_object(dict)
    ctx.obj["config"] = CLIConfig(
        server=server.rstrip("/"),
        token=token,
        actor_id=actor_id,
        actor_type=actor_type,
        timeout=timeout,
    )
    if ctx.invoked_subcommand is None and not ctx.resilient_parsing:
        payload = _home_payload(ctx)
        mode = "human" if _stdout_is_tty() else "json"
        _render_result(payload, mode=mode, human_renderer=lambda: _human_home(payload))


@cli.command("create")
@click.argument("path")
@click.option("--empty", is_flag=True, help="Remove any default cells Jupyter auto-inserts")
@click.option("--json", "json_flag", is_flag=True, help="Force compact JSON output")
@click.option("--pretty", is_flag=True, help="Pretty-print JSON output")
@click.option("--human", "human_flag", is_flag=True, help="Force human-readable output")
@click.pass_context
@_cli_errors
def create_cmd(
    ctx: click.Context,
    path: str,
    empty: bool,
    json_flag: bool,
    pretty: bool,
    human_flag: bool,
) -> None:
    notebook = _sdk_notebook(ctx, path, create=True)
    if empty and getattr(notebook, "_was_created", False):
        for cell in list(notebook.cells):
            cell.delete()
    status = notebook.status()
    payload = _append_hints(
        {
            "command": "create",
            "path": notebook.path,
            "snapshot": _snapshot_from_status(status),
            "summary": _status_summary(status),
            "status": _status_to_dict(status),
        },
        _dedupe_hints(
            [
                _job_hint(f"hypernote status {shlex.quote(notebook.path)}"),
                _job_hint(f"hypernote ix {shlex.quote(notebook.path)} -s 'print(42)'"),
            ]
        ),
    )
    mode = "pretty" if pretty else _final_output_mode(json_flag=json_flag, human_flag=human_flag)
    _render_result(
        payload,
        mode=mode,
        human_renderer=lambda: _with_hints_text(status.summary, payload.get("hints")),
    )


@cli.command("status")
@click.argument("path")
@click.option("--full", is_flag=True, help="Include full cell source and outputs")
@click.option("--failed", "failed_only", is_flag=True, help="Only show failed cells")
@click.option("--query", help="Filter cells by matching text")
@click.option(
    "--max-output",
    default=DEFAULT_OUTPUT_PREVIEW_CHARS,
    show_default=True,
    type=int,
    help="Maximum text chars per output preview",
)
@click.option("--full-output", is_flag=True, help="Show complete output text")
@click.option("--json", "json_flag", is_flag=True, help="Force compact JSON output")
@click.option("--pretty", is_flag=True, help="Pretty-print JSON output")
@click.option("--human", "human_flag", is_flag=True, help="Force human-readable output")
@click.pass_context
@_cli_errors
def status_cmd(
    ctx: click.Context,
    path: str,
    full: bool,
    failed_only: bool,
    query: str | None,
    max_output: int,
    full_output: bool,
    json_flag: bool,
    pretty: bool,
    human_flag: bool,
) -> None:
    notebook = _sdk_notebook(ctx, path)
    status = notebook.status(full=True)
    payload = _build_status_payload(
        status,
        path=notebook.path,
        full=full,
        full_output=full_output,
        failed_only=failed_only,
        query=query,
        max_output_chars=max_output,
    )
    if full_output:
        payload["details"] = _status_to_dict(status)
    payload = _append_hints(payload, _status_hints(notebook.path, snapshot=payload["snapshot"]))
    mode = "pretty" if pretty else _final_output_mode(json_flag=json_flag, human_flag=human_flag)
    _render_result(
        payload,
        mode=mode,
        human_renderer=lambda: _human_status_payload(payload),
    )


@cli.command("diff")
@click.argument("path")
@click.option("--snapshot", "snapshot_token", required=True, help="Snapshot token to diff against")
@click.option("--full", is_flag=True, help="Include full cell source and outputs")
@click.option("--json", "json_flag", is_flag=True, help="Force compact JSON output")
@click.option("--pretty", is_flag=True, help="Pretty-print JSON output")
@click.option("--human", "human_flag", is_flag=True, help="Force human-readable output")
@click.pass_context
@_cli_errors
def diff_cmd(
    ctx: click.Context,
    path: str,
    snapshot_token: str,
    full: bool,
    json_flag: bool,
    pretty: bool,
    human_flag: bool,
) -> None:
    notebook = _sdk_notebook(ctx, path)
    from hypernote import Snapshot

    status = notebook.diff(
        snapshot=Snapshot(token=snapshot_token, timestamp=0.0, cell_count=0),
        full=full,
    )
    payload = {
        "command": "diff",
        "path": notebook.path,
        "snapshot": _snapshot_from_status(status),
        "status": _status_to_dict(status),
    }
    mode = "pretty" if pretty else _final_output_mode(json_flag=json_flag, human_flag=human_flag)
    _render_result(payload, mode=mode, human_renderer=lambda: _human_diff(status, full=full))


@cli.command("cat")
@click.argument("path")
@click.option("--cell", "cell_id", help="Only show a single cell id")
@click.option("--output", "output_cell_id", help="Only show outputs for a single cell id")
@click.option(
    "--tail-output",
    "tail_output_cell_id",
    help="Only show the last output preview for a single cell id",
)
@click.option("--full", is_flag=True, help="Include full cell source and outputs")
@click.option("--no-outputs", is_flag=True, help="Hide outputs")
@click.option(
    "--max-output",
    default=DEFAULT_OUTPUT_PREVIEW_CHARS,
    show_default=True,
    type=int,
    help="Maximum text chars per output preview",
)
@click.option("--full-output", is_flag=True, help="Show complete output text")
@click.option("--json", "json_flag", is_flag=True, help="Force compact JSON output")
@click.option("--pretty", is_flag=True, help="Pretty-print JSON output")
@click.option("--human", "human_flag", is_flag=True, help="Force human-readable output")
@click.pass_context
@_cli_errors
def cat_cmd(
    ctx: click.Context,
    path: str,
    cell_id: str | None,
    output_cell_id: str | None,
    tail_output_cell_id: str | None,
    full: bool,
    no_outputs: bool,
    max_output: int,
    full_output: bool,
    json_flag: bool,
    pretty: bool,
    human_flag: bool,
) -> None:
    selected_modes = sum(
        bool(value) for value in (cell_id, output_cell_id, tail_output_cell_id)
    )
    if selected_modes > 1:
        raise click.ClickException("Choose only one of --cell, --output, or --tail-output")
    notebook = _sdk_notebook(ctx, path)
    payload = _build_cat_payload(
        notebook,
        include_outputs=not no_outputs,
        full_source=full,
        full_output=full_output,
        max_output_chars=max_output,
        cell_id=cell_id,
        output_cell_id=output_cell_id,
        tail_output_cell_id=tail_output_cell_id,
    )
    payload = _append_hints(payload, _cat_hints(notebook.path))
    mode = "pretty" if pretty else _final_output_mode(json_flag=json_flag, human_flag=human_flag)
    _render_result(payload, mode=mode, human_renderer=lambda: _human_cat(payload))


def _run_command_output(
    *,
    notebook: Notebook,
    job: Job,
    command: str,
    path: str,
    inserted_cells: list[CellHandle],
    json_flag: bool,
    pretty: bool,
    human_flag: bool,
    watch: bool,
    stream_json: bool,
    progress: str | None,
    timeout: float | None = None,
) -> None:
    mode = _run_output_mode(
        json_flag=json_flag,
        human_flag=human_flag,
        watch_flag=watch,
        stream_json_flag=stream_json,
    )
    effective_progress = progress or "events"
    if mode in {"watch_human", "stream_json"}:
        watched_job = _watch_job(
            notebook,
            job,
            mode=mode,
            progress=effective_progress,
            inserted_cell_ids=[cell.id for cell in inserted_cells],
            timeout=timeout,
        )
        hints = _job_result_hints(path=path, job=watched_job, inserted_cells=inserted_cells)
        if mode == "stream_json" and hints:
            _emit_stream_json({"event": "hints", "hints": hints})
        elif hints:
            for hint in hints:
                click.echo(f"hint: {hint}")
        return

    if job.status not in TERMINAL_STATUSES and timeout is not None:
        job.wait(timeout=timeout)
    elif job.status not in TERMINAL_STATUSES:
        job.wait()

    payload = _job_result(command, path, job, inserted_cells=inserted_cells)
    if mode == "json":
        _echo_json(payload, pretty=pretty)
    else:
        click.echo(
            _with_hints_text(
                f"Job {job.id} {job.status.value}",
                payload.get("hints"),
            )
        )


@cli.command("ix")
@click.argument("path")
@click.option("-s", "--source", help="Cell source (or pipe via stdin)")
@click.option("--source-file", help="Read source from a file")
@click.option("--cells-json", help="JSON array of cells to insert and execute")
@click.option("--cells-file", help="Read JSON array of cells from a file")
@click.option("--before", help="Insert before an existing cell id")
@click.option("--after", help="Insert after an existing cell id")
@click.option("--no-wait", is_flag=True, help="Return immediately after creating the job")
@click.option("--json", "json_flag", is_flag=True, help="Force compact JSON output")
@click.option("--pretty", is_flag=True, help="Pretty-print JSON output")
@click.option("--human", "human_flag", is_flag=True, help="Force human-readable output")
@click.option("--watch", is_flag=True, help="Force attached human-readable progress")
@click.option("--stream-json", is_flag=True, help="Force JSONL event streaming")
@click.option("--progress", type=PROGRESS_CHOICES, help="Streaming verbosity")
@click.option("--timeout", type=float, help="Maximum seconds to wait")
@click.pass_context
@_cli_errors
def ix_cmd(
    ctx: click.Context,
    path: str,
    source: str | None,
    source_file: str | None,
    cells_json: str | None,
    cells_file: str | None,
    before: str | None,
    after: str | None,
    no_wait: bool,
    json_flag: bool,
    pretty: bool,
    human_flag: bool,
    watch: bool,
    stream_json: bool,
    progress: str | None,
    timeout: float | None,
) -> None:
    notebook = _sdk_notebook(ctx, path)
    cells = _read_cells_payload(
        source=source,
        source_file=source_file,
        cells_json=cells_json,
        cells_file=cells_file,
        default_cell_type="code",
    )
    if no_wait and len(cells) > 1:
        raise click.ClickException("batch ix does not support --no-wait")

    inserted_cells: list[CellHandle] = []
    current_before = before
    current_after = after
    final_job: Job | None = None
    halted_early = False
    halt_reason: str | None = None

    for index, cell_spec in enumerate(cells):
        inserted = _insert_cell(
            notebook,
            cell_spec,
            before=current_before,
            after=current_after,
        )
        inserted_cells.append(inserted)
        current_before = None
        current_after = inserted.id
        if inserted.type == CellType.CODE:
            job = inserted.run()
            final_job = job
            if no_wait:
                break
            _run_command_output(
                notebook=notebook,
                job=job,
                command="ix",
                path=path,
                inserted_cells=[inserted],
                json_flag=json_flag if len(cells) == 1 else True,
                pretty=pretty,
                human_flag=human_flag if len(cells) == 1 else False,
                watch=watch if len(cells) == 1 else False,
                stream_json=stream_json if len(cells) == 1 else False,
                progress=progress,
                timeout=timeout,
            )
            if job.status in {JobStatus.FAILED, JobStatus.INTERRUPTED, JobStatus.AWAITING_INPUT}:
                halted_early = index < len(cells) - 1
                halt_reason = _halt_reason(job.status) if halted_early else None
                break

    if len(cells) > 1:
        payload = {
            "command": "ix",
            "path": path,
            "status": "error"
            if final_job and final_job.status in {JobStatus.FAILED, JobStatus.INTERRUPTED}
            else "ok",
            "cells_inserted": len(inserted_cells),
            "cells_total": len(cells),
            "results": [
                {
                    "cell_id": cell.id,
                    "cell_type": cell.type.value,
                    "execution_count": cell.execution_count,
                    "outputs": list(cell.outputs),
                }
                for cell in inserted_cells
            ],
        }
        if halted_early and halt_reason is not None and inserted_cells:
            payload["halt_reason"] = halt_reason
            payload["last_processed_cell_id"] = inserted_cells[-1].id
            payload["cells_remaining"] = len(cells) - len(inserted_cells)
        _echo_json(payload, pretty=pretty or _stdout_is_tty())
        return

    if final_job is None:
        payload = {
            "command": "ix",
            "path": path,
            "inserted_cells": [_cell_payload(cell) for cell in inserted_cells],
            "status": "ok",
        }
        mode = "pretty" if pretty else _final_output_mode(
            json_flag=json_flag,
            human_flag=human_flag,
        )
        _render_result(
            payload,
            mode=mode,
            human_renderer=lambda: f"Inserted {inserted_cells[0].id}",
        )
        return

    if no_wait:
        payload = _job_result("ix", path, final_job, inserted_cells=inserted_cells)
        _echo_json(payload, pretty=pretty or _stdout_is_tty())


@cli.command("exec")
@click.argument("path")
@click.argument("cell_ids", nargs=-1)
@click.option("--no-wait", is_flag=True, help="Return immediately after creating the job")
@click.option("--json", "json_flag", is_flag=True, help="Force compact JSON output")
@click.option("--pretty", is_flag=True, help="Pretty-print JSON output")
@click.option("--human", "human_flag", is_flag=True, help="Force human-readable output")
@click.option("--watch", is_flag=True, help="Force attached human-readable progress")
@click.option("--stream-json", is_flag=True, help="Force JSONL event streaming")
@click.option("--progress", type=PROGRESS_CHOICES, help="Streaming verbosity")
@click.option("--timeout", type=float, help="Maximum seconds to wait")
@click.pass_context
@_cli_errors
def exec_cmd(
    ctx: click.Context,
    path: str,
    cell_ids: tuple[str, ...],
    no_wait: bool,
    json_flag: bool,
    pretty: bool,
    human_flag: bool,
    watch: bool,
    stream_json: bool,
    progress: str | None,
    timeout: float | None,
) -> None:
    if not cell_ids:
        raise click.ClickException("Provide at least one cell id")
    notebook = _sdk_notebook(ctx, path)
    for cell_id in cell_ids:
        cell = notebook.cells[cell_id]
        if cell.type != CellType.CODE:
            raise click.ClickException(f"Cell {cell_id} is {cell.type.value}, not code")

    job = notebook.run(*cell_ids)
    if no_wait:
        _echo_json(
            _job_result("exec", path, job, inserted_cells=[]),
            pretty=pretty or _stdout_is_tty(),
        )
        return

    _run_command_output(
        notebook=notebook,
        job=job,
        command="exec",
        path=path,
        inserted_cells=[],
        json_flag=json_flag,
        pretty=pretty,
        human_flag=human_flag,
        watch=watch,
        stream_json=stream_json,
        progress=progress,
        timeout=timeout,
    )


@cli.command("run-all")
@click.argument("path")
@click.option("--json", "json_flag", is_flag=True, help="Force compact JSON output")
@click.option("--pretty", is_flag=True, help="Pretty-print JSON output")
@click.option("--human", "human_flag", is_flag=True, help="Force human-readable output")
@click.option("--watch", is_flag=True, help="Force attached human-readable progress")
@click.option("--stream-json", is_flag=True, help="Force JSONL event streaming")
@click.option("--progress", type=PROGRESS_CHOICES, help="Streaming verbosity")
@click.option("--timeout", type=float, help="Maximum seconds to wait")
@click.pass_context
@_cli_errors
def run_all_cmd(
    ctx: click.Context,
    path: str,
    json_flag: bool,
    pretty: bool,
    human_flag: bool,
    watch: bool,
    stream_json: bool,
    progress: str | None,
    timeout: float | None,
) -> None:
    notebook = _sdk_notebook(ctx, path)
    job = notebook.run_all()
    _run_command_output(
        notebook=notebook,
        job=job,
        command="run-all",
        path=path,
        inserted_cells=[],
        json_flag=json_flag,
        pretty=pretty,
        human_flag=human_flag,
        watch=watch,
        stream_json=stream_json,
        progress=progress,
        timeout=timeout,
    )


@cli.command("restart")
@click.argument("path")
@click.option("--json", "json_flag", is_flag=True, help="Force compact JSON output")
@click.option("--pretty", is_flag=True, help="Pretty-print JSON output")
@click.option("--human", "human_flag", is_flag=True, help="Force human-readable output")
@click.pass_context
@_cli_errors
def restart_cmd(
    ctx: click.Context,
    path: str,
    json_flag: bool,
    pretty: bool,
    human_flag: bool,
) -> None:
    notebook = _sdk_notebook(ctx, path)
    runtime = notebook.restart()
    payload = {"command": "restart", "path": path, "runtime": _runtime_to_dict(runtime)}
    mode = "pretty" if pretty else _final_output_mode(json_flag=json_flag, human_flag=human_flag)
    _render_result(payload, mode=mode, human_renderer=lambda: f"Restarted {path}")


@cli.command("restart-run-all")
@click.argument("path")
@click.option("--json", "json_flag", is_flag=True, help="Force compact JSON output")
@click.option("--pretty", is_flag=True, help="Pretty-print JSON output")
@click.option("--human", "human_flag", is_flag=True, help="Force human-readable output")
@click.option("--watch", is_flag=True, help="Force attached human-readable progress")
@click.option("--stream-json", is_flag=True, help="Force JSONL event streaming")
@click.option("--progress", type=PROGRESS_CHOICES, help="Streaming verbosity")
@click.option("--timeout", type=float, help="Maximum seconds to wait")
@click.pass_context
@_cli_errors
def restart_run_all_cmd(
    ctx: click.Context,
    path: str,
    json_flag: bool,
    pretty: bool,
    human_flag: bool,
    watch: bool,
    stream_json: bool,
    progress: str | None,
    timeout: float | None,
) -> None:
    notebook = _sdk_notebook(ctx, path)
    notebook.restart()
    job = notebook.run_all()
    _run_command_output(
        notebook=notebook,
        job=job,
        command="restart-run-all",
        path=path,
        inserted_cells=[],
        json_flag=json_flag,
        pretty=pretty,
        human_flag=human_flag,
        watch=watch,
        stream_json=stream_json,
        progress=progress,
        timeout=timeout,
    )


@cli.command("interrupt")
@click.argument("path")
@click.option("--json", "json_flag", is_flag=True, help="Force compact JSON output")
@click.option("--pretty", is_flag=True, help="Pretty-print JSON output")
@click.option("--human", "human_flag", is_flag=True, help="Force human-readable output")
@click.pass_context
@_cli_errors
def interrupt_cmd(
    ctx: click.Context,
    path: str,
    json_flag: bool,
    pretty: bool,
    human_flag: bool,
) -> None:
    notebook = _sdk_notebook(ctx, path)
    notebook.interrupt()
    payload = {"command": "interrupt", "path": path, "sent": True}
    mode = "pretty" if pretty else _final_output_mode(json_flag=json_flag, human_flag=human_flag)
    _render_result(payload, mode=mode, human_renderer=lambda: f"Interrupted {path}")


@cli.group("edit")
def edit_group() -> None:
    """Mutate notebook cells without executing them."""


def _edit_output(
    *,
    payload: dict[str, Any],
    json_flag: bool,
    pretty: bool,
    human_flag: bool,
    human_text: str,
) -> None:
    mode = "pretty" if pretty else _final_output_mode(json_flag=json_flag, human_flag=human_flag)
    _render_result(payload, mode=mode, human_renderer=lambda: human_text)


@edit_group.command("insert-code")
@click.argument("path")
@click.option("-s", "--source", help="Cell source (or pipe via stdin)")
@click.option("--source-file", help="Read source from a file")
@click.option("--id", "cell_id", help="Explicit cell id")
@click.option("--before", help="Insert before an existing cell id")
@click.option("--after", help="Insert after an existing cell id")
@click.option("--json", "json_flag", is_flag=True)
@click.option("--pretty", is_flag=True)
@click.option("--human", "human_flag", is_flag=True)
@click.pass_context
@_cli_errors
def edit_insert_code_cmd(
    ctx: click.Context,
    path: str,
    source: str | None,
    source_file: str | None,
    cell_id: str | None,
    before: str | None,
    after: str | None,
    json_flag: bool,
    pretty: bool,
    human_flag: bool,
) -> None:
    notebook = _sdk_notebook(ctx, path)
    cell = notebook.cells.insert_code(
        _read_text(source, source_file),
        id=cell_id,
        before=before,
        after=after,
    )
    _edit_output(
        payload={"command": "edit.insert-code", "path": path, "cell": _cell_payload(cell)},
        json_flag=json_flag,
        pretty=pretty,
        human_flag=human_flag,
        human_text=f"Inserted {cell.id}",
    )


@edit_group.command("insert-markdown")
@click.argument("path")
@click.option("-s", "--source", help="Cell source (or pipe via stdin)")
@click.option("--source-file", help="Read source from a file")
@click.option("--id", "cell_id", help="Explicit cell id")
@click.option("--before", help="Insert before an existing cell id")
@click.option("--after", help="Insert after an existing cell id")
@click.option("--json", "json_flag", is_flag=True)
@click.option("--pretty", is_flag=True)
@click.option("--human", "human_flag", is_flag=True)
@click.pass_context
@_cli_errors
def edit_insert_markdown_cmd(
    ctx: click.Context,
    path: str,
    source: str | None,
    source_file: str | None,
    cell_id: str | None,
    before: str | None,
    after: str | None,
    json_flag: bool,
    pretty: bool,
    human_flag: bool,
) -> None:
    notebook = _sdk_notebook(ctx, path)
    cell = notebook.cells.insert_markdown(
        _read_text(source, source_file),
        id=cell_id,
        before=before,
        after=after,
    )
    _edit_output(
        payload={"command": "edit.insert-markdown", "path": path, "cell": _cell_payload(cell)},
        json_flag=json_flag,
        pretty=pretty,
        human_flag=human_flag,
        human_text=f"Inserted {cell.id}",
    )


@edit_group.command("replace")
@click.argument("path")
@click.argument("cell_id")
@click.option("-s", "--source", help="Cell source (or pipe via stdin)")
@click.option("--source-file", help="Read source from a file")
@click.option("--json", "json_flag", is_flag=True)
@click.option("--pretty", is_flag=True)
@click.option("--human", "human_flag", is_flag=True)
@click.pass_context
@_cli_errors
def edit_replace_cmd(
    ctx: click.Context,
    path: str,
    cell_id: str,
    source: str | None,
    source_file: str | None,
    json_flag: bool,
    pretty: bool,
    human_flag: bool,
) -> None:
    notebook = _sdk_notebook(ctx, path)
    cell = notebook.cells[cell_id].replace(_read_text(source, source_file))
    _edit_output(
        payload={"command": "edit.replace", "path": path, "cell": _cell_payload(cell)},
        json_flag=json_flag,
        pretty=pretty,
        human_flag=human_flag,
        human_text=f"Replaced {cell.id}",
    )


@edit_group.command("move")
@click.argument("path")
@click.argument("cell_id")
@click.option("--before", help="Move before an existing cell id")
@click.option("--after", help="Move after an existing cell id")
@click.option("--json", "json_flag", is_flag=True)
@click.option("--pretty", is_flag=True)
@click.option("--human", "human_flag", is_flag=True)
@click.pass_context
@_cli_errors
def edit_move_cmd(
    ctx: click.Context,
    path: str,
    cell_id: str,
    before: str | None,
    after: str | None,
    json_flag: bool,
    pretty: bool,
    human_flag: bool,
) -> None:
    notebook = _sdk_notebook(ctx, path)
    notebook.cells[cell_id].move(before=before, after=after)
    _edit_output(
        payload={"command": "edit.move", "path": path, "cell_id": cell_id, "moved": True},
        json_flag=json_flag,
        pretty=pretty,
        human_flag=human_flag,
        human_text=f"Moved {cell_id}",
    )


@edit_group.command("delete")
@click.argument("path")
@click.argument("cell_id")
@click.option("--json", "json_flag", is_flag=True)
@click.option("--pretty", is_flag=True)
@click.option("--human", "human_flag", is_flag=True)
@click.pass_context
@_cli_errors
def edit_delete_cmd(
    ctx: click.Context,
    path: str,
    cell_id: str,
    json_flag: bool,
    pretty: bool,
    human_flag: bool,
) -> None:
    notebook = _sdk_notebook(ctx, path)
    notebook.cells[cell_id].delete()
    _edit_output(
        payload={"command": "edit.delete", "path": path, "cell_id": cell_id, "deleted": True},
        json_flag=json_flag,
        pretty=pretty,
        human_flag=human_flag,
        human_text=f"Deleted {cell_id}",
    )


@edit_group.command("clear-outputs")
@click.argument("path")
@click.argument("cell_id")
@click.option("--json", "json_flag", is_flag=True)
@click.option("--pretty", is_flag=True)
@click.option("--human", "human_flag", is_flag=True)
@click.pass_context
@_cli_errors
def edit_clear_outputs_cmd(
    ctx: click.Context,
    path: str,
    cell_id: str,
    json_flag: bool,
    pretty: bool,
    human_flag: bool,
) -> None:
    notebook = _sdk_notebook(ctx, path)
    cell = notebook.cells[cell_id].clear_outputs()
    _edit_output(
        payload={"command": "edit.clear-outputs", "path": path, "cell": _cell_payload(cell)},
        json_flag=json_flag,
        pretty=pretty,
        human_flag=human_flag,
        human_text=f"Cleared outputs for {cell.id}",
    )


@cli.group("runtime")
def runtime_group() -> None:
    """Inspect and control notebook runtimes."""


@runtime_group.command("status")
@click.argument("path")
@click.option("--pretty", is_flag=True)
@click.pass_context
@_cli_errors
def runtime_status_cmd(ctx: click.Context, path: str, pretty: bool) -> None:
    notebook = _sdk_notebook(ctx, path)
    _echo_json(
        {"runtime": notebook.runtime.to_dict(), "path": path},
        pretty=pretty or _stdout_is_tty(),
    )


@runtime_group.command("ensure")
@click.argument("path")
@click.option("--pretty", is_flag=True)
@click.pass_context
@_cli_errors
def runtime_ensure_cmd(ctx: click.Context, path: str, pretty: bool) -> None:
    notebook = _sdk_notebook(ctx, path)
    runtime = notebook.runtime.ensure()
    _echo_json({"runtime": runtime.to_dict(), "path": path}, pretty=pretty or _stdout_is_tty())


@runtime_group.command("stop")
@click.argument("path")
@click.option("--pretty", is_flag=True)
@click.pass_context
@_cli_errors
def runtime_stop_cmd(ctx: click.Context, path: str, pretty: bool) -> None:
    notebook = _sdk_notebook(ctx, path)
    runtime = notebook.runtime.stop()
    _echo_json({"runtime": runtime.to_dict(), "path": path}, pretty=pretty or _stdout_is_tty())


@cli.group("job")
def job_group() -> None:
    """Inspect and control Hypernote jobs."""


@job_group.command("get")
@click.argument("job_id")
@click.option("--pretty", is_flag=True)
@click.pass_context
@_cli_errors
def job_get_cmd(ctx: click.Context, job_id: str, pretty: bool) -> None:
    _echo_json(_sdk_control(ctx).get_job_payload(job_id), pretty=pretty or _stdout_is_tty())


@job_group.command("await")
@click.argument("job_id")
@click.option("--timeout", default=60.0, show_default=True, type=float)
@click.option("--json", "json_flag", is_flag=True, help="Force compact JSON output")
@click.option("--pretty", is_flag=True, help="Pretty-print JSON output")
@click.option("--human", "human_flag", is_flag=True, help="Force human-readable output")
@click.option("--watch", is_flag=True, help="Force attached human-readable progress")
@click.option("--stream-json", is_flag=True, help="Force JSONL event streaming")
@click.option("--progress", type=PROGRESS_CHOICES, help="Streaming verbosity")
@click.pass_context
@_cli_errors
def job_await_cmd(
    ctx: click.Context,
    job_id: str,
    timeout: float,
    json_flag: bool,
    pretty: bool,
    human_flag: bool,
    watch: bool,
    stream_json: bool,
    progress: str | None,
) -> None:
    job = _sdk_control(ctx).get_job(job_id)
    _run_command_output(
        notebook=job.notebook,
        job=job,
        command="job.await",
        path=job.notebook_path,
        inserted_cells=[],
        json_flag=json_flag,
        pretty=pretty,
        human_flag=human_flag,
        watch=watch,
        stream_json=stream_json,
        progress=progress,
        timeout=timeout,
    )


@job_group.command("stdin")
@click.argument("job_id")
@click.option("--value", required=True, help="stdin value to send")
@click.option("--pretty", is_flag=True)
@click.pass_context
@_cli_errors
def job_stdin_cmd(ctx: click.Context, job_id: str, value: str, pretty: bool) -> None:
    _echo_json(_sdk_control(ctx).send_job_stdin(job_id, value), pretty=pretty or _stdout_is_tty())


@cli.group("setup")
def setup_group() -> None:
    """Operator diagnostics."""


@setup_group.command("serve")
@click.option(
    "--root",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=Path.cwd,
    show_default="current working directory",
    help="Repo root to expose through Jupyter",
)
@click.option("--host", help="Server host. Defaults to the host from --server.")
@click.option("--port", type=int, help="Server port. Defaults to the port from --server.")
@click.option(
    "--browser/--no-browser",
    default=False,
    show_default=True,
    help="Whether to open a browser tab",
)
@click.pass_context
@_cli_errors
def setup_serve_cmd(
    ctx: click.Context,
    root: Path,
    host: str | None,
    port: int | None,
    browser: bool,
) -> None:
    _require_jupyterlab()
    cfg = _ctx_config(ctx)
    default_host, default_port = _server_host_port(cfg.server)
    resolved_root = root.resolve()
    if not resolved_root.exists():
        raise click.ClickException(f"Root directory does not exist: {resolved_root}")

    serve_host = host or default_host
    serve_port = port or default_port
    token = cfg.token or ""
    cmd = _serve_command(
        root=resolved_root,
        host=serve_host,
        port=serve_port,
        token=token,
        no_browser=not browser,
    )
    url = f"http://{serve_host}:{serve_port}"
    click.echo(f"Starting Hypernote Jupyter server at {url}")
    click.echo(f"Root: {resolved_root}")
    click.echo("Extensions: hypernote, jupyter_server_nbmodel, jupyter_server_ydoc")
    completed = subprocess.run(cmd, cwd=str(resolved_root), check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


@setup_group.command("doctor")
@click.option("--path", help="Notebook path to inspect against the live server")
@click.pass_context
@_cli_errors
def setup_doctor_cmd(ctx: click.Context, path: str | None) -> None:
    cfg = _ctx_config(ctx)
    report: dict[str, object] = {"server": cfg.server, "hypernote_api": "unreachable"}
    control = _sdk_control(ctx)
    try:
        control.list_jobs()
        report["hypernote_api"] = "ok"
        report["jobs_endpoint"] = True
    except Exception as exc:  # pragma: no cover - exercised via CLI output
        report["error"] = str(exc)

    if report.get("hypernote_api") == "ok":
        try:
            default_spec = control.get_kernelspec("python3")
            launcher = _kernelspec_launcher(default_spec)
            if launcher is not None:
                report["default_kernel"] = launcher
        except Exception as exc:  # pragma: no cover - best-effort enrichment
            report["default_kernel_error"] = str(exc)

    if path and report.get("hypernote_api") == "ok":
        report["path"] = path
        try:
            document = control.get_notebook_document(path, content=True)
            notebook_kernel = _kernelspec_name_from_document(document)
            report["notebook_kernelspec"] = notebook_kernel

            runtime = control.get_runtime_status(path)
            report["runtime_state"] = runtime.get("state")
            report["runtime_kernel_name"] = runtime.get("kernel_name")

            try:
                kernelspec = control.get_kernelspec(notebook_kernel)
            except Exception as exc:  # pragma: no cover - networked failure surface
                report["kernelspec_error"] = str(exc)
            else:
                launcher = _kernelspec_launcher(kernelspec)
                if launcher is not None:
                    report["kernelspec_launcher"] = launcher

            runtime_kernel = runtime.get("kernel_name")
            if runtime_kernel and runtime_kernel != notebook_kernel:
                report["warnings"] = [
                    "Live runtime kernel does not match notebook metadata. "
                    "Stop or restart the runtime to pick up the notebook's kernelspec."
                ]
        except Exception as exc:
            report["path_error"] = str(exc)
    _echo_json(report, pretty=_stdout_is_tty())
