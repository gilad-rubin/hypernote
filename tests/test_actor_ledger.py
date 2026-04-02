"""Tests for MemoryLedger job tracking and cell attribution."""

import json

import pytest

from hypernote.actor_ledger import (
    ActorType,
    JobAction,
    JobStatus,
    MemoryLedger,
    MemoryLedgerPolicy,
)


@pytest.fixture
async def ledger():
    ledger = MemoryLedger()
    await ledger.initialize()
    yield ledger
    await ledger.close()


async def test_create_and_get_job(ledger: MemoryLedger):
    job = await ledger.create_job(
        notebook_id="nb-1",
        actor_id="agent-1",
        actor_type=ActorType.AGENT,
        action=JobAction.EXECUTE,
        target_cells=json.dumps(["cell-0", "cell-1"]),
    )
    assert job.status == JobStatus.QUEUED
    assert job.actor_id == "agent-1"

    fetched = await ledger.get_job(job.job_id)
    assert fetched is not None
    assert fetched.notebook_id == "nb-1"
    assert fetched.target_cells == json.dumps(["cell-0", "cell-1"])
    assert fetched.request_uids == []


async def test_update_job_status_lifecycle(ledger: MemoryLedger):
    job = await ledger.create_job(
        notebook_id="nb-1",
        actor_id="user-gilad",
        actor_type=ActorType.HUMAN,
        action=JobAction.EXECUTE,
    )
    assert job.started_at is None

    await ledger.update_job_status(job.job_id, JobStatus.RUNNING, runtime_id="rt-1")
    updated = await ledger.get_job(job.job_id)
    assert updated.status == JobStatus.RUNNING
    assert updated.started_at is not None
    assert updated.runtime_id == "rt-1"

    await ledger.update_job_status(job.job_id, JobStatus.SUCCEEDED)
    done = await ledger.get_job(job.job_id)
    assert done.status == JobStatus.SUCCEEDED
    assert done.completed_at is not None


async def test_append_request_uid(ledger: MemoryLedger):
    job = await ledger.create_job(
        notebook_id="nb-1",
        actor_id="agent-1",
        actor_type=ActorType.AGENT,
        action=JobAction.EXECUTE,
    )
    await ledger.append_request_uid(job.job_id, "uid-1")
    await ledger.append_request_uid(job.job_id, "uid-2")

    fetched = await ledger.get_job(job.job_id)
    assert fetched is not None
    assert fetched.request_uids == ["uid-1", "uid-2"]


async def test_list_jobs_filters(ledger: MemoryLedger):
    await ledger.create_job("nb-1", "a1", ActorType.AGENT, JobAction.EXECUTE)
    await ledger.create_job("nb-1", "a2", ActorType.AGENT, JobAction.EXECUTE)
    await ledger.create_job("nb-2", "a1", ActorType.AGENT, JobAction.EXECUTE)

    nb1_jobs = await ledger.list_jobs(notebook_id="nb-1")
    assert len(nb1_jobs) == 2

    all_jobs = await ledger.list_jobs()
    assert len(all_jobs) == 3


async def test_list_active_jobs(ledger: MemoryLedger):
    j1 = await ledger.create_job("nb-1", "a1", ActorType.AGENT, JobAction.EXECUTE)
    j2 = await ledger.create_job("nb-1", "a2", ActorType.AGENT, JobAction.EXECUTE)
    await ledger.update_job_status(j1.job_id, JobStatus.SUCCEEDED)

    active = await ledger.list_active_jobs("nb-1")
    assert len(active) == 1
    assert active[0].job_id == j2.job_id


async def test_cell_attribution(ledger: MemoryLedger):
    await ledger.update_cell_attribution(
        "nb-1", "cell-0",
        editor_id="agent-1", editor_type=ActorType.AGENT,
    )
    attr = await ledger.get_cell_attribution("nb-1", "cell-0")
    assert attr.last_editor_id == "agent-1"
    assert attr.last_executor_id is None

    # Update executor without overwriting editor
    await ledger.update_cell_attribution(
        "nb-1", "cell-0",
        executor_id="user-gilad", executor_type=ActorType.HUMAN,
    )
    attr = await ledger.get_cell_attribution("nb-1", "cell-0")
    assert attr.last_editor_id == "agent-1"
    assert attr.last_executor_id == "user-gilad"


async def test_list_cell_attributions(ledger: MemoryLedger):
    await ledger.update_cell_attribution("nb-1", "c0", editor_id="a1", editor_type=ActorType.AGENT)
    await ledger.update_cell_attribution("nb-1", "c1", editor_id="a2", editor_type=ActorType.AGENT)

    attrs = await ledger.list_cell_attributions("nb-1")
    assert len(attrs) == 2


async def test_get_nonexistent_job(ledger: MemoryLedger):
    assert await ledger.get_job("nonexistent") is None


async def test_get_nonexistent_attribution(ledger: MemoryLedger):
    assert await ledger.get_cell_attribution("nb-x", "cell-x") is None


async def test_prunes_completed_job_history_per_notebook():
    ledger = MemoryLedger(MemoryLedgerPolicy(max_completed_jobs_per_notebook=1))
    await ledger.initialize()

    first = await ledger.create_job("nb-1", "a1", ActorType.AGENT, JobAction.EXECUTE)
    second = await ledger.create_job("nb-1", "a2", ActorType.AGENT, JobAction.EXECUTE)
    await ledger.update_job_status(first.job_id, JobStatus.SUCCEEDED)
    await ledger.update_job_status(second.job_id, JobStatus.SUCCEEDED)

    jobs = await ledger.list_jobs(notebook_id="nb-1")
    assert [job.job_id for job in jobs] == [second.job_id]
    assert await ledger.get_job(first.job_id) is None

    await ledger.close()


async def test_evict_notebook_clears_jobs_and_attribution(ledger: MemoryLedger):
    job = await ledger.create_job("nb-1", "a1", ActorType.AGENT, JobAction.EXECUTE)
    await ledger.update_cell_attribution(
        "nb-1",
        "cell-1",
        editor_id="a1",
        editor_type=ActorType.AGENT,
    )

    await ledger.evict_notebook("nb-1")

    assert await ledger.get_job(job.job_id) is None
    assert await ledger.list_jobs(notebook_id="nb-1") == []
    assert await ledger.get_cell_attribution("nb-1", "cell-1") is None
