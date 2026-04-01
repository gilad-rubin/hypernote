"""Hypernote CLI: command-line interface for notebook operations.

Families: observe, edit, execute, jobs, runtime, checkpoints, workspace, setup.
All commands talk to the Hypernote REST API.
"""

from __future__ import annotations

import asyncio
import json
import sys
from functools import wraps

import click
import httpx


def async_command(f):
    """Decorator to run async click commands."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))
    return wrapper


def _client(ctx: click.Context) -> httpx.AsyncClient:
    base = ctx.obj.get("base_url", "http://127.0.0.1:8888")
    actor_id = ctx.obj.get("actor_id", "cli-user")
    actor_type = ctx.obj.get("actor_type", "human")
    return httpx.AsyncClient(
        base_url=f"{base}/hypernote/api",
        headers={
            "X-Hypernote-Actor-Id": actor_id,
            "X-Hypernote-Actor-Type": actor_type,
        },
        timeout=30,
    )


def _print_json(data):
    click.echo(json.dumps(data, indent=2, default=str))


@click.group()
@click.option("--server", default="http://127.0.0.1:8888", help="Jupyter server URL")
@click.option("--actor-id", default="cli-user", help="Actor identity")
@click.option("--actor-type", default="human", type=click.Choice(["human", "agent"]))
@click.pass_context
def cli(ctx, server, actor_id, actor_type):
    """Hypernote: server-owned notebook execution with actor attribution."""
    ctx.ensure_object(dict)
    ctx.obj["base_url"] = server
    ctx.obj["actor_id"] = actor_id
    ctx.obj["actor_type"] = actor_type


# =============================================================================
# observe: notebook inspection
# =============================================================================

@cli.group()
def observe():
    """Inspect notebooks and cells."""
    pass


@observe.command("cat")
@click.argument("notebook_id")
@click.pass_context
@async_command
async def observe_cat(ctx, notebook_id):
    """Display all cells in a notebook."""
    async with _client(ctx) as c:
        resp = await c.get(f"/notebooks/{notebook_id}/cells")
        resp.raise_for_status()
        cells = resp.json()["cells"]
        for cell in cells:
            marker = "In" if cell["type"] == "code" else "Md"
            click.echo(f"[{marker} {cell['id']}]:")
            click.echo(cell["source"])
            click.echo()


@observe.command("status")
@click.argument("notebook_id")
@click.pass_context
@async_command
async def observe_status(ctx, notebook_id):
    """Show notebook runtime status and active jobs."""
    async with _client(ctx) as c:
        rt = await c.get(f"/notebooks/{notebook_id}/runtime")
        rt.raise_for_status()
        _print_json(rt.json())

        jobs = await c.get(f"/jobs?notebook_id={notebook_id}")
        jobs.raise_for_status()
        active = [j for j in jobs.json()["jobs"] if j["status"] in ("queued", "running", "awaiting_input")]
        if active:
            click.echo(f"\nActive jobs: {len(active)}")
            for j in active:
                click.echo(f"  {j['job_id']}: {j['status']} by {j['actor_id']}")


@observe.command("list")
@click.pass_context
@async_command
async def observe_list(ctx):
    """List available notebooks."""
    async with _client(ctx) as c:
        resp = await c.get("/notebooks")
        resp.raise_for_status()
        _print_json(resp.json())


# =============================================================================
# edit: cell mutation
# =============================================================================

@cli.group()
def edit():
    """Edit notebook cells."""
    pass


@edit.command("insert")
@click.argument("notebook_id")
@click.option("--index", "-i", default=0, help="Insert position")
@click.option("--type", "cell_type", default="code", type=click.Choice(["code", "markdown"]))
@click.option("--source", "-s", required=True, help="Cell source code")
@click.pass_context
@async_command
async def edit_insert(ctx, notebook_id, index, cell_type, source):
    """Insert a new cell."""
    async with _client(ctx) as c:
        resp = await c.post(f"/notebooks/{notebook_id}/cells", json={
            "index": index, "cell_type": cell_type, "source": source,
        })
        resp.raise_for_status()
        _print_json(resp.json())


@edit.command("replace")
@click.argument("notebook_id")
@click.argument("cell_id")
@click.option("--source", "-s", required=True, help="New source code")
@click.pass_context
@async_command
async def edit_replace(ctx, notebook_id, cell_id, source):
    """Replace cell source."""
    async with _client(ctx) as c:
        resp = await c.put(f"/notebooks/{notebook_id}/cells/{cell_id}", json={"source": source})
        resp.raise_for_status()
        click.echo("Updated.")


@edit.command("delete")
@click.argument("notebook_id")
@click.argument("cell_id")
@click.pass_context
@async_command
async def edit_delete(ctx, notebook_id, cell_id):
    """Delete a cell."""
    async with _client(ctx) as c:
        resp = await c.delete(f"/notebooks/{notebook_id}/cells/{cell_id}")
        resp.raise_for_status()
        click.echo("Deleted.")


@edit.command("clear")
@click.argument("notebook_id")
@click.pass_context
@async_command
async def edit_clear(ctx, notebook_id):
    """Clear all cells from a notebook."""
    async with _client(ctx) as c:
        resp = await c.get(f"/notebooks/{notebook_id}/cells")
        resp.raise_for_status()
        for cell in resp.json()["cells"]:
            await c.delete(f"/notebooks/{notebook_id}/cells/{cell['id']}")
        click.echo("Cleared.")


# =============================================================================
# execute: run cells
# =============================================================================

@cli.group()
def execute():
    """Execute notebook cells."""
    pass


@execute.command("cell")
@click.argument("notebook_id")
@click.argument("cell_ids", nargs=-1, required=True)
@click.option("--wait/--no-wait", default=True, help="Wait for completion")
@click.pass_context
@async_command
async def execute_cell(ctx, notebook_id, cell_ids, wait):
    """Execute specific cells."""
    async with _client(ctx) as c:
        resp = await c.post(f"/notebooks/{notebook_id}/execute", json={
            "cell_ids": list(cell_ids),
        })
        resp.raise_for_status()
        job = resp.json()
        click.echo(f"Job {job['job_id']}: {job['status']}")

        if wait:
            await _await_job(c, job["job_id"])


@execute.command("run-all")
@click.argument("notebook_id")
@click.option("--wait/--no-wait", default=True)
@click.pass_context
@async_command
async def execute_run_all(ctx, notebook_id, wait):
    """Execute all cells in order."""
    async with _client(ctx) as c:
        cells_resp = await c.get(f"/notebooks/{notebook_id}/cells")
        cells_resp.raise_for_status()
        cell_ids = [cell["id"] for cell in cells_resp.json()["cells"] if cell["type"] == "code"]

        if not cell_ids:
            click.echo("No code cells.")
            return

        resp = await c.post(f"/notebooks/{notebook_id}/execute", json={"cell_ids": cell_ids})
        resp.raise_for_status()
        job = resp.json()
        click.echo(f"Job {job['job_id']}: {job['status']} ({len(cell_ids)} cells)")

        if wait:
            await _await_job(c, job["job_id"])


@execute.command("insert-and-run")
@click.argument("notebook_id")
@click.option("--source", "-s", required=True)
@click.option("--index", "-i", default=-1, help="Insert position (-1 = append)")
@click.option("--wait/--no-wait", default=True)
@click.pass_context
@async_command
async def execute_insert_and_run(ctx, notebook_id, source, index, wait):
    """Insert a cell and immediately execute it."""
    async with _client(ctx) as c:
        if index == -1:
            cells_resp = await c.get(f"/notebooks/{notebook_id}/cells")
            cells_resp.raise_for_status()
            index = len(cells_resp.json()["cells"])

        ins = await c.post(f"/notebooks/{notebook_id}/cells", json={
            "index": index, "cell_type": "code", "source": source,
        })
        ins.raise_for_status()
        cell_id = ins.json()["cell_id"]

        resp = await c.post(f"/notebooks/{notebook_id}/execute", json={"cell_ids": [cell_id]})
        resp.raise_for_status()
        job = resp.json()
        click.echo(f"Cell {cell_id}, Job {job['job_id']}")

        if wait:
            await _await_job(c, job["job_id"])


@execute.command("restart")
@click.argument("notebook_id")
@click.pass_context
@async_command
async def execute_restart(ctx, notebook_id):
    """Restart the kernel."""
    async with _client(ctx) as c:
        await c.post(f"/notebooks/{notebook_id}/runtime/stop", json={})
        resp = await c.post(f"/notebooks/{notebook_id}/runtime/open", json={"client_id": "cli"})
        resp.raise_for_status()
        click.echo(f"Runtime restarted: {resp.json()['state']}")


@execute.command("interrupt")
@click.argument("notebook_id")
@click.pass_context
@async_command
async def execute_interrupt(ctx, notebook_id):
    """Interrupt running execution."""
    async with _client(ctx) as c:
        resp = await c.post(f"/notebooks/{notebook_id}/interrupt", json={})
        resp.raise_for_status()
        click.echo("Interrupted.")


# =============================================================================
# jobs: job tracking
# =============================================================================

@cli.group()
def jobs():
    """Track execution jobs."""
    pass


@jobs.command("get")
@click.argument("job_id")
@click.pass_context
@async_command
async def jobs_get(ctx, job_id):
    """Get job details."""
    async with _client(ctx) as c:
        resp = await c.get(f"/jobs/{job_id}")
        resp.raise_for_status()
        _print_json(resp.json())


@jobs.command("list")
@click.option("--notebook-id", "-n", help="Filter by notebook")
@click.option("--status", "-s", help="Filter by status")
@click.pass_context
@async_command
async def jobs_list(ctx, notebook_id, status):
    """List jobs."""
    async with _client(ctx) as c:
        params = {}
        if notebook_id:
            params["notebook_id"] = notebook_id
        if status:
            params["status"] = status
        resp = await c.get("/jobs", params=params)
        resp.raise_for_status()
        for j in resp.json()["jobs"]:
            click.echo(f"  {j['job_id']}: {j['status']} by {j['actor_id']} ({j['action']})")


@jobs.command("await")
@click.argument("job_id")
@click.option("--timeout", default=60, help="Timeout in seconds")
@click.pass_context
@async_command
async def jobs_await(ctx, job_id, timeout):
    """Wait for a job to complete."""
    async with _client(ctx) as c:
        result = await _await_job(c, job_id, timeout)
        _print_json(result)


@jobs.command("send-stdin")
@click.argument("job_id")
@click.argument("value")
@click.pass_context
@async_command
async def jobs_send_stdin(ctx, job_id, value):
    """Send stdin input for a job awaiting input."""
    async with _client(ctx) as c:
        resp = await c.post(f"/jobs/{job_id}/stdin", json={"value": value})
        resp.raise_for_status()
        click.echo("Sent.")


# =============================================================================
# runtime: kernel lifecycle
# =============================================================================

@cli.group()
def runtime():
    """Manage notebook runtimes."""
    pass


@runtime.command("status")
@click.argument("notebook_id")
@click.pass_context
@async_command
async def runtime_status(ctx, notebook_id):
    """Get runtime status."""
    async with _client(ctx) as c:
        resp = await c.get(f"/notebooks/{notebook_id}/runtime")
        resp.raise_for_status()
        _print_json(resp.json())


@runtime.command("open")
@click.argument("notebook_id")
@click.pass_context
@async_command
async def runtime_open(ctx, notebook_id):
    """Open or attach to a runtime."""
    async with _client(ctx) as c:
        resp = await c.post(f"/notebooks/{notebook_id}/runtime/open", json={"client_id": "cli"})
        resp.raise_for_status()
        _print_json(resp.json())


@runtime.command("stop")
@click.argument("notebook_id")
@click.pass_context
@async_command
async def runtime_stop(ctx, notebook_id):
    """Stop the runtime."""
    async with _client(ctx) as c:
        resp = await c.post(f"/notebooks/{notebook_id}/runtime/stop", json={})
        resp.raise_for_status()
        click.echo("Stopped.")


@runtime.command("recover")
@click.argument("notebook_id")
@click.pass_context
@async_command
async def runtime_recover(ctx, notebook_id):
    """Recover a detached runtime if still alive."""
    async with _client(ctx) as c:
        resp = await c.get(f"/notebooks/{notebook_id}/runtime")
        resp.raise_for_status()
        status = resp.json()
        if status["state"] in ("live-attached", "live-detached"):
            resp = await c.post(f"/notebooks/{notebook_id}/runtime/open", json={"client_id": "cli"})
            resp.raise_for_status()
            click.echo(f"Recovered: {resp.json()['state']}")
        else:
            click.echo(f"Runtime is {status['state']} — cannot recover.")


# =============================================================================
# checkpoints: notebook versioning
# =============================================================================

@cli.group()
def checkpoints():
    """Manage notebook checkpoints."""
    pass


@checkpoints.command("create")
@click.argument("notebook_id")
@click.pass_context
@async_command
async def checkpoints_create(ctx, notebook_id):
    """Create a checkpoint."""
    # Checkpoint operations would go through Jupyter's checkpoints API
    click.echo(f"Checkpoint created for {notebook_id} (placeholder — uses Jupyter checkpoints API)")


@checkpoints.command("list")
@click.argument("notebook_id")
@click.pass_context
@async_command
async def checkpoints_list(ctx, notebook_id):
    """List checkpoints."""
    click.echo(f"Checkpoints for {notebook_id} (placeholder — uses Jupyter checkpoints API)")


@checkpoints.command("restore")
@click.argument("notebook_id")
@click.argument("checkpoint_id")
@click.pass_context
@async_command
async def checkpoints_restore(ctx, notebook_id, checkpoint_id):
    """Restore a checkpoint."""
    click.echo(f"Restored {notebook_id} to checkpoint {checkpoint_id} (placeholder)")


@checkpoints.command("delete")
@click.argument("notebook_id")
@click.argument("checkpoint_id")
@click.pass_context
@async_command
async def checkpoints_delete(ctx, notebook_id, checkpoint_id):
    """Delete a checkpoint."""
    click.echo(f"Deleted checkpoint {checkpoint_id} (placeholder)")


# =============================================================================
# workspace: document management
# =============================================================================

@cli.group()
def workspace():
    """Manage workspace documents."""
    pass


@workspace.command("open")
@click.argument("path")
@click.pass_context
@async_command
async def workspace_open(ctx, path):
    """Open a notebook by path."""
    async with _client(ctx) as c:
        resp = await c.post("/notebooks", json={"path": path})
        resp.raise_for_status()
        _print_json(resp.json())


@workspace.command("list")
@click.pass_context
@async_command
async def workspace_list(ctx):
    """List workspace documents."""
    async with _client(ctx) as c:
        resp = await c.get("/notebooks")
        resp.raise_for_status()
        _print_json(resp.json())


# =============================================================================
# setup: onboarding and diagnostics
# =============================================================================

@cli.group()
def setup():
    """Setup and diagnostics."""
    pass


@setup.command("doctor")
@click.pass_context
@async_command
async def setup_doctor(ctx):
    """Check Hypernote server health."""
    base = ctx.obj["base_url"]
    click.echo(f"Checking {base}...")
    try:
        async with httpx.AsyncClient(base_url=base, timeout=5) as c:
            resp = await c.get("/api/status")
            if resp.status_code == 200:
                click.echo("  Jupyter Server: OK")
            else:
                click.echo(f"  Jupyter Server: {resp.status_code}")
    except httpx.ConnectError:
        click.echo("  Jupyter Server: NOT REACHABLE")

    click.echo(f"  Actor: {ctx.obj['actor_id']} ({ctx.obj['actor_type']})")


@setup.command("mcp-status")
@click.pass_context
def setup_mcp_status(ctx):
    """Show MCP configuration status."""
    click.echo("MCP server: hypernote.mcp.server")
    click.echo("Transport: STDIO or Streamable HTTP")
    click.echo("Use: hypernote-mcp (when configured)")


# =============================================================================
# Helpers
# =============================================================================

async def _await_job(c: httpx.AsyncClient, job_id: str, timeout: int = 60) -> dict:
    """Poll until job reaches terminal state."""
    import time
    deadline = time.time() + timeout
    terminal = {"succeeded", "failed", "interrupted"}
    while time.time() < deadline:
        resp = await c.get(f"/jobs/{job_id}")
        resp.raise_for_status()
        job = resp.json()
        status = job["status"]
        if status in terminal:
            click.echo(f"Job {job_id}: {status}")
            return job
        await asyncio.sleep(0.5)
    click.echo(f"Job {job_id}: timeout")
    return {"job_id": job_id, "status": "timeout"}
