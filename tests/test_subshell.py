"""Tests for hypernote.server.subshell.

These exercise a real ipykernel because the subshell mechanism is a kernel-side
feature and mocking it would not prove anything useful. They verify that:

* `ensure_subshell` returns a non-None subshell id from an IPython kernel.
* `install_subshell_routing` injects `subshell_id` into outgoing
  `execute_request` headers.
* While an `execute_request` is busy on the subshell, the kernel still answers
  `kernel_info_request` on the main shell within a tight latency budget.

The third assertion is the load-bearing one — it is the entire reason this
module exists.
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable

import pytest
from jupyter_client import AsyncKernelManager

from hypernote.server.subshell import (
    ensure_subshell,
    has_subshell,
    install_subshell_routing,
    interrupt_subshell,
    register_restart_hook,
    reset_subshell_state,
)


async def _read_until_parent(channel, request_id: str, timeout: float):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            msg = await asyncio.wait_for(
                channel.get_msg(timeout=min(remaining, 0.5)),
                timeout=remaining + 0.5,
            )
        except (asyncio.TimeoutError, TimeoutError):
            continue
        except Exception:
            continue
        if msg.get("parent_header", {}).get("msg_id") == request_id:
            return msg
    raise TimeoutError(f"no reply for {request_id} within {timeout}s")


@pytest.fixture
async def kernel_setup():
    km = AsyncKernelManager(kernel_name="python3")
    await km.start_kernel()
    client = km.client()
    client.start_channels()
    await client.wait_for_ready()
    try:
        yield km, client
    finally:
        client.stop_channels()
        await km.shutdown_kernel(now=True)


@pytest.fixture
async def kernel_client(kernel_setup):
    _km, client = kernel_setup
    yield client


async def _wait_for_iopub_busy(client, parent_msg_id: str, timeout: float = 5.0):
    """Wait until the kernel reports execution_state=busy for the given parent."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            msg = await asyncio.wait_for(
                client.iopub_channel.get_msg(timeout=min(remaining, 0.5)),
                timeout=remaining,
            )
        except (asyncio.TimeoutError, TimeoutError):
            continue
        if (
            msg.get("msg_type") == "status"
            and msg.get("parent_header", {}).get("msg_id") == parent_msg_id
            and msg.get("content", {}).get("execution_state") == "busy"
        ):
            return msg
    raise TimeoutError(f"kernel did not report busy for {parent_msg_id} within {timeout}s")


async def test_ensure_subshell_returns_id_for_ipykernel(kernel_client):
    subshell_id = await ensure_subshell(kernel_client)
    assert isinstance(subshell_id, str)
    assert subshell_id


async def test_ensure_subshell_is_idempotent(kernel_client):
    first = await ensure_subshell(kernel_client)
    second = await ensure_subshell(kernel_client)
    assert first == second


async def test_install_subshell_routing_injects_subshell_id(kernel_client):
    await ensure_subshell(kernel_client)
    install_subshell_routing(kernel_client)

    sent_messages: list[dict] = []
    original_send = kernel_client.shell_channel.send

    def capture(msg):
        sent_messages.append(msg)
        return original_send(msg)

    kernel_client.shell_channel.send = capture
    try:
        kernel_client.execute("pass")
    finally:
        kernel_client.shell_channel.send = original_send

    assert len(sent_messages) == 1
    header = sent_messages[0]["header"]
    assert header.get("subshell_id") is not None
    assert header["msg_type"] == "execute_request"


async def test_kernel_info_unblocked_while_subshell_busy(kernel_client):
    """The point of this whole module: kernel_info on main shell stays fast."""
    subshell_id = await ensure_subshell(kernel_client)
    assert subshell_id is not None

    install_subshell_routing(kernel_client)

    long_running = "import time; time.sleep(8); print('done')"
    exec_msg_id = kernel_client.execute(long_running, allow_stdin=False)

    # Wait until the kernel actually reports the subshell is busy. Without
    # this, sleeping a fixed interval can pass for the wrong reason — kernel
    # still on main shell, kernel_info trivially fast, but the test never
    # exercised the subshell path.
    await _wait_for_iopub_busy(kernel_client, exec_msg_id, timeout=5.0)

    info_request = kernel_client.session.msg("kernel_info_request", {})
    info_id = info_request["header"]["msg_id"]
    info_start = time.monotonic()
    kernel_client.shell_channel.send(info_request)

    info_reply = await _read_until_parent(
        kernel_client.shell_channel, info_id, timeout=3.0
    )
    info_elapsed = time.monotonic() - info_start

    assert info_reply["content"]["status"] == "ok"
    assert info_elapsed < 1.0, (
        f"kernel_info_reply took {info_elapsed:.2f}s — main shell was blocked "
        "even though execute was supposed to be on a subshell."
    )

    # And the execute eventually completes — proves we didn't break execution.
    exec_reply = await _read_until_parent(
        kernel_client.shell_channel, exec_msg_id, timeout=15.0
    )
    assert exec_reply["content"]["status"] == "ok"


async def test_reset_subshell_state_drops_cache(kernel_client):
    subshell_id = await ensure_subshell(kernel_client)
    assert subshell_id is not None

    reset_subshell_state(kernel_client)
    assert not has_subshell(kernel_client)
    new_id = await ensure_subshell(kernel_client)
    assert new_id is not None
    assert new_id != subshell_id


class _StubKernelManager:
    """Minimal stand-in for MultiKernelManager.add_restart_callback."""

    def __init__(self):
        self.callbacks: list[tuple[str, Callable[[], None], str]] = []

    def add_restart_callback(self, kernel_id, callback, event="restart"):
        self.callbacks.append((kernel_id, callback, event))


async def test_interrupt_subshell_terminates_busy_cell(kernel_client):
    """The point of this test: ipykernel's interrupt_request ignores subshell_id
    and only interrupts the main thread. We work around that by raising
    KeyboardInterrupt in the subshell thread via PyThreadState_SetAsyncExc.
    """
    subshell_id = await ensure_subshell(kernel_client)
    assert subshell_id is not None
    install_subshell_routing(kernel_client)

    long_code = (
        "import time\n"
        "for i in range(20):\n"
        "    time.sleep(1)\n"
        "print('did NOT interrupt')\n"
    )
    exec_msg_id = kernel_client.execute(long_code, allow_stdin=False)
    await _wait_for_iopub_busy(kernel_client, exec_msg_id, timeout=5.0)

    interrupt_start = time.monotonic()
    interrupt_subshell(kernel_client, subshell_id)

    exec_reply = await _read_until_parent(
        kernel_client.shell_channel, exec_msg_id, timeout=5.0
    )
    interrupt_elapsed = time.monotonic() - interrupt_start

    assert exec_reply["content"]["status"] == "error"
    assert exec_reply["content"].get("ename") == "KeyboardInterrupt"
    assert interrupt_elapsed < 3.0, (
        f"interrupt took {interrupt_elapsed:.2f}s — should be sub-second"
    )


async def test_register_restart_hook_clears_subshell_on_restart(kernel_client):
    """Simulating the autorestarter firing should drop the cached subshell id."""
    subshell_id = await ensure_subshell(kernel_client)
    assert subshell_id is not None
    install_subshell_routing(kernel_client)

    km_stub = _StubKernelManager()
    register_restart_hook(km_stub, "test-kernel", kernel_client)

    # Idempotent — a second registration is a no-op.
    register_restart_hook(km_stub, "test-kernel", kernel_client)
    assert len(km_stub.callbacks) == 1
    kernel_id, callback, event = km_stub.callbacks[0]
    assert kernel_id == "test-kernel"
    assert event == "restart"

    callback()
    assert not has_subshell(kernel_client)

    # Routing should still be installed; the next ensure_subshell should
    # request a new id from the (still-running) kernel.
    new_id = await ensure_subshell(kernel_client)
    assert new_id is not None
    assert new_id != subshell_id
