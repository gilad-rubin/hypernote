"""Subshell routing for Hypernote-driven kernel execution.

Background: when a Jupyter kernel is busy executing a cell on the main shell
channel, any other shell-channel request from a concurrent client (e.g. a
JupyterLab tab opening mid-run) blocks behind it. JupyterLab's notebook init
issues `kernel_info_request` on shell, so opening Lab while Hypernote is
running a long cell stalls Lab's notebook UI until the cell completes.

ipykernel 7+ supports subshells (JEP 91). A subshell is a separate handler
thread on the kernel side; an `execute_request` whose header includes
`subshell_id` runs there instead of the main shell, leaving the main shell
free to answer `kernel_info_request`, `comm_info_request`, etc. immediately.

This module provides four operations against a `jupyter_client`
`AsyncKernelClient`:

* `ensure_subshell(client)` — sends `create_subshell_request` on the kernel's
  control channel and caches the returned `subshell_id` on the client. The
  control channel is unblocked by an in-flight `execute_request`, so this
  works even mid-run.
* `install_subshell_routing(client)` — replaces `client.execute` with a
  variant that injects `header["subshell_id"]` into outgoing
  `execute_request` messages. nbmodel's `_async_execute_interactive` calls
  `self.execute(...)` internally, so it picks up the routing transparently.
* `reset_subshell_state(client)` — clears the cached subshell id so the next
  `ensure_subshell` requests a fresh one. Required after kernel restart,
  because the old subshell does not survive the new kernel process.
* `register_restart_hook(kernel_manager, kernel_id, client)` — installs a
  Jupyter kernel-restart callback that calls `reset_subshell_state` so an
  autorestart does not leave a stale subshell id wired into the client.

Why monkey-patch instead of subclass? nbmodel constructs the kernel client
via `km.client()` from the per-kernel `KernelManager`, outside Hypernote's
control. Configuring `kernel_manager_class.client_class` globally would
affect every kernel client in the Jupyter Server, including those Hypernote
does not drive. Patching the specific client instance Hypernote uses is the
narrower seam, at the cost of relying on the upstream call shape
`_async_execute_interactive` -> `self.execute(...)`.

The routing is best-effort: if the kernel does not support subshells
(non-IPython kernels), `ensure_subshell` returns `None` and routing falls
back to the main shell.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_SUBSHELL_ID_ATTR = "_hypernote_subshell_id"
_ROUTING_ATTR = "_hypernote_routing_installed"
_RESTART_HOOK_ATTR = "_hypernote_restart_hook_installed"
_SUBSHELL_TIMEOUT_SECONDS = 5.0


async def ensure_subshell(client: Any) -> str | None:
    """Ensure the client has an associated subshell, creating one if needed.

    Returns the subshell id, or ``None`` if the kernel does not support
    subshells.
    """
    cached = getattr(client, _SUBSHELL_ID_ATTR, None)
    if cached is not None:
        return cached

    request = client.session.msg("create_subshell_request", {})
    request_id = request["header"]["msg_id"]
    client.control_channel.send(request)

    deadline = time.monotonic() + _SUBSHELL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            reply = await asyncio.wait_for(
                client.control_channel.get_msg(timeout=remaining),
                timeout=remaining,
            )
        except (asyncio.TimeoutError, TimeoutError):
            break
        if reply.get("parent_header", {}).get("msg_id") != request_id:
            continue
        content = reply.get("content", {})
        if content.get("status") != "ok" or "subshell_id" not in content:
            logger.info(
                "subshell create rejected by kernel; falling back to main shell (status=%s)",
                content.get("status"),
            )
            return None
        subshell_id = content["subshell_id"]
        client._hypernote_subshell_id = subshell_id  # noqa: SLF001 - intentional patch attr
        logger.info("subshell created: %s", subshell_id)
        return subshell_id

    logger.warning(
        "subshell create timed out after %ss; falling back to main shell",
        _SUBSHELL_TIMEOUT_SECONDS,
    )
    return None


def install_subshell_routing(client: Any) -> None:
    """Replace ``client.execute`` so outgoing execute_requests target the subshell.

    Idempotent. If no subshell id is present on the client, the patched
    method behaves identically to the original (sends to main shell).
    """
    if getattr(client, _ROUTING_ATTR, False):
        return

    def execute_via_subshell(
        code: str,
        silent: bool = False,
        store_history: bool = True,
        user_expressions: dict[str, Any] | None = None,
        allow_stdin: bool | None = None,
        stop_on_error: bool = True,
    ) -> str:
        if user_expressions is None:
            user_expressions = {}
        if allow_stdin is None:
            allow_stdin = client.allow_stdin
        if not isinstance(code, str):
            raise ValueError(f"code {code!r} must be a string")
        content = {
            "code": code,
            "silent": silent,
            "store_history": store_history,
            "user_expressions": user_expressions,
            "allow_stdin": allow_stdin,
            "stop_on_error": stop_on_error,
        }
        subshell_id = getattr(client, _SUBSHELL_ID_ATTR, None)
        if subshell_id is None:
            msg = client.session.msg("execute_request", content)
        else:
            header = client.session.msg_header("execute_request")
            header["subshell_id"] = subshell_id
            msg = client.session.msg("execute_request", content, header=header)
        client.shell_channel.send(msg)
        return msg["header"]["msg_id"]

    client.execute = execute_via_subshell
    client._hypernote_routing_installed = True  # noqa: SLF001 - intentional patch attr


def reset_subshell_state(client: Any) -> None:
    """Clear cached subshell state on the client (e.g. after kernel restart).

    Routing remains installed; the next `ensure_subshell` call will request a
    new subshell against the new kernel.
    """
    if hasattr(client, _SUBSHELL_ID_ATTR):
        delattr(client, _SUBSHELL_ID_ATTR)


def has_subshell(client: Any) -> bool:
    """Whether the client currently has a cached subshell id."""
    return getattr(client, _SUBSHELL_ID_ATTR, None) is not None


def interrupt_subshell(client: Any, subshell_id: str) -> None:
    """Raise KeyboardInterrupt in the subshell's thread.

    Background: ipykernel 7.2's `interrupt_request` ignores `subshell_id` and
    just calls `os.kill(pid, SIGINT)`, which only interrupts the kernel's main
    thread. Subshells run in their own threads with their own asyncio loops,
    so a process-wide SIGINT does not affect them. JEP 91 specifies
    per-subshell interrupt as a future direction; until ipykernel implements
    it, we do this ourselves.

    The trick: while a subshell is busy executing user code, the kernel's
    main shell is idle. We send a small Python snippet on the main shell that
    looks up the subshell's thread by name (`subshell-<id>`) and calls
    `ctypes.pythonapi.PyThreadState_SetAsyncExc(thread_id, KeyboardInterrupt)`.
    CPython raises that exception in the target thread at the next bytecode
    boundary, terminating the running cell.

    `PyThreadState_SetAsyncExc` is documented as "use with care" — it can
    leave C-extension state inconsistent if the target thread is mid-call
    into a C extension. For interrupting Python-level cells this is
    acceptable; production-grade kernel hardening would want an upstream
    ipykernel fix that signals subshells natively.

    The snippet is sent as an ordinary `execute_request` on the main shell
    (no `subshell_id`), bypassing the routing patch installed by
    `install_subshell_routing` by building the message and sending via the
    shell channel directly.
    """
    snippet = (
        "import ctypes as _ctypes\n"
        "import threading as _threading\n"
        f"_target_name = 'subshell-{subshell_id}'\n"
        "_target = None\n"
        "for _t in _threading.enumerate():\n"
        "    if _t.name == _target_name:\n"
        "        _target = _t\n"
        "        break\n"
        "if _target is not None:\n"
        "    _ctypes.pythonapi.PyThreadState_SetAsyncExc(\n"
        "        _ctypes.c_ulong(_target.ident),\n"
        "        _ctypes.py_object(KeyboardInterrupt),\n"
        "    )\n"
    )
    content = {
        "code": snippet,
        "silent": True,
        "store_history": False,
        "user_expressions": {},
        "allow_stdin": False,
        "stop_on_error": True,
    }
    msg = client.session.msg("execute_request", content)  # main shell, no subshell_id
    client.shell_channel.send(msg)


def register_restart_hook(kernel_manager: Any, kernel_id: str, client: Any) -> None:
    """Register a Jupyter kernel-restart callback that resets subshell state.

    Idempotent per (client, kernel_id) — installs at most once.

    The cached `subshell_id` does not survive a kernel process restart. nbmodel
    keeps its kernel client cached by `kernel_id` across autorestarts, so
    without this hook a stale id keeps getting injected into execute_request
    headers against a kernel that no longer recognizes it.
    """
    if getattr(client, _RESTART_HOOK_ATTR, False):
        return

    def on_restart() -> None:
        logger.info("kernel %s restarted; resetting Hypernote subshell state", kernel_id)
        reset_subshell_state(client)

    kernel_manager.add_restart_callback(kernel_id, on_restart)
    client._hypernote_restart_hook_installed = True  # noqa: SLF001 - intentional patch attr
