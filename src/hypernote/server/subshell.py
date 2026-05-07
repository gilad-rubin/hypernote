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
_ORIGINAL_EXECUTE_ATTR = "_hypernote_original_execute"
_SUBSHELL_TIMEOUT_SECONDS = 5.0

# Name-mangled attributes Hypernote reaches into on jupyter_server_nbmodel's
# ExecutionStack. Used by `cleanup_after_restart` and verified at extension
# startup so a name change in nbmodel surfaces as a startup warning rather
# than as a silent cleanup no-op.
NBMODEL_PRIVATE_ATTRS: tuple[str, ...] = (
    "_ExecutionStack__kernel_clients",
    "_ExecutionStack__workers",
    "_ExecutionStack__tasks",
    "_ExecutionStack__execution_results",
    "_ExecutionStack__pending_inputs",
)


def validate_nbmodel_internals(execution_stack: Any) -> None:
    """Log a warning if nbmodel's expected internal attributes are missing.

    `cleanup_after_restart` reaches into name-mangled internals of
    `jupyter_server_nbmodel.execution_stack.ExecutionStack`. If nbmodel
    refactors any of those attributes, our cleanup silently no-ops and the
    "Lab Restart leaves the kernel ready" invariant breaks invisibly. Run
    this once at extension startup so a missing attribute is surfaced as a
    loud single warning rather than discovered later via a stuck restart.
    """
    missing = [name for name in NBMODEL_PRIVATE_ATTRS if not hasattr(execution_stack, name)]
    if missing:
        logger.warning(
            "jupyter_server_nbmodel.ExecutionStack is missing expected attrs %s; "
            "Hypernote restart cleanup will silently no-op for those. "
            "An nbmodel update may have refactored its internals.",
            missing,
        )


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
        except Exception:
            # The module promises "returns None if the kernel does not
            # support subshells" — honour that on any non-timeout error
            # (e.g. zmq socket closed, malformed message) so callers fall
            # back to the main shell instead of seeing an unhandled
            # exception.
            logger.warning(
                "unexpected error reading subshell create reply; falling back to main shell",
                exc_info=True,
            )
            return None
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

    # Stash the upstream execute so callers / tests can revert if needed.
    if not hasattr(client, _ORIGINAL_EXECUTE_ATTR):
        client._hypernote_original_execute = client.execute  # noqa: SLF001 - intentional

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


def interrupt_subshell(client: Any, subshell_id: str) -> str:
    """Raise KeyboardInterrupt in the subshell's thread.

    Returns the `msg_id` of the main-shell snippet so callers can wait for
    its `execute_reply` if they need to confirm the interrupt actually
    landed. Returning the id (rather than `None`) lets callers distinguish
    "send raised" from "send succeeded but reply unknown".

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
    into a C extension. For pure-Python and most numpy/pandas cells the
    interrupt fires within a bytecode boundary; cells currently inside a
    long blocking C call without a Python yield point (e.g. a `requests`
    call with no timeout, a synchronous DB query) only get interrupted
    once control returns to Python. Production-grade kernel hardening
    would want an upstream ipykernel fix that signals subshells natively.

    The snippet is fire-and-forget — we send it on the main shell and do
    not wait for an execute_reply. If the main shell is currently busy
    (e.g. a human ran a Lab cell concurrently), the snippet queues behind
    that work and the interrupt fires once it lands. In Hypernote's normal
    flow Hypernote routes everything to the subshell, so the main shell is
    idle and latency is sub-second.

    The snippet is sent as an ordinary `execute_request` on the main shell
    (no `subshell_id`), bypassing the routing patch installed by
    `install_subshell_routing` by building the message and sending via the
    shell channel directly. ``subshell_id`` is interpolated via ``repr`` so
    a malformed value cannot escape the string literal as Python code.
    """
    target_name_literal = repr(f"subshell-{subshell_id}")
    # Belt-and-suspenders: if the subshell thread cannot be found (stale
    # cached id, race with restart) or PyThreadState_SetAsyncExc reports
    # failure (return value != 1), fall back to a process-wide SIGINT in
    # the snippet itself. SIGINT will interrupt whatever Python code is
    # running on the main thread (which is just this snippet), so the
    # only side effect is the snippet itself exiting with
    # KeyboardInterrupt — and the cell on the subshell either already
    # received its KeyboardInterrupt or, in the fallback case, will
    # receive it via the SIGINT path that ipykernel applies to all
    # threads at safe Python boundaries.
    snippet = (
        "import ctypes as _ctypes\n"
        "import os as _os\n"
        "import signal as _signal\n"
        "import threading as _threading\n"
        f"_target_name = {target_name_literal}\n"
        "_target = next(\n"
        "    (_t for _t in _threading.enumerate() if _t.name == _target_name),\n"
        "    None,\n"
        ")\n"
        "_handled = False\n"
        "if _target is not None:\n"
        "    _rv = _ctypes.pythonapi.PyThreadState_SetAsyncExc(\n"
        "        _ctypes.c_ulong(_target.ident),\n"
        "        _ctypes.py_object(KeyboardInterrupt),\n"
        "    )\n"
        "    _handled = _rv == 1\n"
        "if not _handled:\n"
        "    _os.kill(_os.getpid(), _signal.SIGINT)\n"
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
    return msg["header"]["msg_id"]


def register_restart_hook(kernel_manager: Any, kernel_id: str, client: Any) -> None:
    """Register an autorestarter callback that resets Hypernote subshell state.

    Idempotent per (client, kernel_id) — installs at most once.

    Note: this only fires on the autorestarter's unexpected-death detection.
    Explicit restart via `POST /api/kernels/{id}/restart` does not fire
    these callbacks; that path is handled by the
    `KernelRestartInterceptHandler`, which calls `cleanup_after_restart`.
    """
    if getattr(client, _RESTART_HOOK_ATTR, False):
        return

    add_callback = getattr(kernel_manager, "add_restart_callback", None)
    if add_callback is None:
        # Custom / minimal kernel managers may not expose autorestarter
        # callbacks. The explicit-restart route override still gives us
        # cleanup; the autorestarter case just becomes a no-op for that
        # deployment. Skip silently rather than raising AttributeError
        # from inside _ensure_kernel_client_ready.
        logger.debug(
            "kernel_manager %r has no add_restart_callback; "
            "skipping Hypernote autorestart hook for kernel %s",
            type(kernel_manager).__name__,
            kernel_id,
        )
        return

    def on_restart() -> None:
        logger.info("kernel %s autorestarted; resetting Hypernote subshell state", kernel_id)
        reset_subshell_state(client)

    add_callback(kernel_id, on_restart)
    client._hypernote_restart_hook_installed = True  # noqa: SLF001 - intentional patch attr


def cleanup_after_restart(execution_stack: Any, kernel_id: str) -> None:
    """Tear down nbmodel and Hypernote state for a kernel that was just restarted.

    Called from the explicit-restart route override after the kernel process
    has been killed and a new one started under the same `kernel_id`.

    nbmodel keeps a kernel client and an asyncio worker task per kernel_id.
    After restart the old client's ZMQ channels point to the dead process and
    the worker is stuck on a read from a dead socket. Without eviction the
    next execute would queue behind the stuck worker and never run.

    We:
      1. Cancel and remove the worker task.
      2. Stop channels on the cached client and remove it.
      3. Reset Hypernote's subshell-id cache on the (now removed) client so
         the next run builds a fresh subshell on the new kernel.
    """
    workers = getattr(execution_stack, "_ExecutionStack__workers", None)
    if workers is not None:
        worker = workers.pop(kernel_id, None)
        if worker is not None and not worker.done():
            try:
                worker.cancel()
            except Exception:
                logger.debug("could not cancel nbmodel worker", exc_info=True)

    clients = getattr(execution_stack, "_ExecutionStack__kernel_clients", None)
    if clients is not None:
        client = clients.pop(kernel_id, None)
        if client is not None:
            try:
                client.stop_channels()
            except Exception:
                logger.debug("could not stop_channels on stale client", exc_info=True)
            reset_subshell_state(client)
            # Drop the routing-installed flag so the next client (built fresh
            # by nbmodel's _get_client) gets a clean install.
            for attr in (_ROUTING_ATTR, _RESTART_HOOK_ATTR):
                if hasattr(client, attr):
                    try:
                        delattr(client, attr)
                    except Exception:
                        logger.debug(
                            "could not delete attr %s on stale client",
                            attr,
                            exc_info=True,
                        )

    tasks = getattr(execution_stack, "_ExecutionStack__tasks", None)
    if tasks is not None:
        tasks.pop(kernel_id, None)
    results = getattr(execution_stack, "_ExecutionStack__execution_results", None)
    if results is not None:
        results.pop(kernel_id, None)
    pending = getattr(execution_stack, "_ExecutionStack__pending_inputs", None)
    if pending is not None:
        pending.pop(kernel_id, None)

    logger.info("cleared Hypernote/nbmodel state for restarted kernel %s", kernel_id)
