# HyperNote Browser Regression Spec

## Goal

Add a durable browser regression suite for HyperNote's single-truth notebook behavior.

The suite must prove that notebook edits, execution, streaming output, and notebook UI state all reflect one logical document truth, regardless of whether JupyterLab is open, closed, or opened later.

## Feature Definition

The system should support this workflow consistently:

1. Create or connect to a notebook headlessly through the SDK.
2. Insert or edit cells through the SDK.
3. Run cells headlessly through the SDK.
4. Open the notebook in JupyterLab at any point before, during, or after execution.
5. Observe the exact same notebook state, execution state, and output history that would have been visible if the notebook had been open from the start.

Opening a notebook must attach a viewer to the current notebook state. It must not change correctness, restart execution, or create a second truth source.

## Core Invariant

Notebook edits and execution must use one logical document truth, regardless of whether JupyterLab is open, closed, or opened later.

## Acceptance Criteria

### Immediate executability

- If the SDK inserts a new code cell, `cell.run()` works immediately.
- No save, refresh, reopen, or runtime restart is required.
- This must hold whether the notebook is closed, already open, or opened later.

### Open vs. closed parity

- The same SDK sequence produces the same execution result whether the notebook is closed or already open in JupyterLab.
- There must be no special-case recovery step such as refresh, reopen, or runtime restart.

### Live open-tab behavior

- If a notebook is already open and the SDK inserts a new cell, the new cell appears in the open tab without refresh.
- When that cell starts running, the tab shows the running state.
- While the cell is running, streamed output appears progressively.
- After completion, the tab shows the final outputs and final execution state.

### Persistence after headless execution

- After SDK insertion and execution, the notebook stores the correct `execution_count` and outputs.
- Reopening the notebook later shows the same outputs and state.

### Streaming behavior

- Long-running inserted cells show partial output before completion.
- Later output appears without rerunning the cell.
- Final output matches the completed run.

### Job and UI coherence

- When `cell.run()` returns a running job, the notebook UI shows that same cell as running.
- While the job is running, the notebook shows output growth for that cell.
- When the job succeeds, the notebook shows the final state for that cell.
- There must be no case where a visible inserted cell cannot execute or a job fails silently.

### Observation API correctness

- `nb.diff(snapshot=...)` after insertion reports `ADDED`.
- After source edits it reports `SOURCE_EDITED`.
- After execution it reports `OUTPUT_CHANGED` and/or `EXECUTION_COUNT`.
- This must work in both closed-notebook and open-notebook cases.

### Late-open correctness

- If a long-running cell starts headlessly and the notebook is opened mid-execution, the notebook immediately shows the output already produced so far.
- The running cell shows the correct running state on first render.
- New output continues to stream after the notebook is opened.
- Opening late does not restart execution, duplicate output, or change runtime/job identity.

## End-to-End Scenarios

### Scenario A: Closed notebook

1. Connect to a notebook through the SDK.
2. Insert a new code cell.
3. Run it.
4. Wait for completion.
5. Open the notebook in JupyterLab.
6. Verify the output is present and correct.

### Scenario B: Open notebook

1. Open a notebook in JupyterLab.
2. Insert a new code cell through the SDK.
3. Verify the new cell appears live.
4. Run it through the SDK.
5. Verify the open tab shows running state, output, and final completion state.

### Scenario C: Open notebook with streaming

1. Open a notebook in JupyterLab.
2. Insert a long-running print loop through the SDK.
3. Run it through the SDK.
4. Verify the open tab shows partial output before completion, continued output growth during the run, and final output after completion.

### Scenario D: Late-open during streaming

1. Insert a long-running code cell through the SDK.
2. Start `cell.run()` headlessly.
3. Wait until output has already been produced.
4. Open the notebook in JupyterLab mid-execution.
5. Verify prior output is already visible on first render.
6. Verify later output continues streaming.
7. Verify final output matches the completed run.

### Scenario E: Existing-cell regression

1. Run a pre-existing code cell.
2. Verify prior execution behavior still works exactly as before.

## Fast, Parallel, Reliable Test Strategy

### Layer 1: Unit tests

- Keep SDK transport and notebook-state logic under fast unit tests.
- Mock document reads, mutation responses, and diff snapshots.
- Assert enum values, error mapping, and diff classification without starting a server.

### Layer 2: Live integration tests

- Start one fresh JupyterLab server per test session or per worker group.
- Use unique notebook paths per test.
- Exercise SDK plus HyperNote server endpoints directly.
- Verify cell insertion, execution, persistence, runtime state, and diff semantics without a browser.

### Layer 3: Browser regression tests

- Use browser tests only for behaviors that need real UI confirmation:
  - cell appears live in an open tab
  - running state is visible
  - streamed output appears while the job is still running
  - late-open shows already-produced output immediately
- Reuse one server per worker when possible, but isolate notebooks per test.
- Use a unique JupyterLab workspace URL per test to avoid restored-tab pollution.
- Keep streaming cells short but long enough to prove intermediate output, usually around 8 to 12 seconds for CI.
- Assert on concrete rendered output markers that do not appear in the cell source itself.
- Pair browser checks with SDK-side job polling so failures can distinguish UI lag from execution failure.

### Parallelization rules

- Every test must use a unique notebook path.
- Every browser test must use a unique workspace path, for example `/lab/workspaces/<unique>/tree/<notebook>`.
- Shared server state is allowed only when notebooks and workspaces are isolated per test.
- Tests must not depend on notebook ordering or output produced by another test.

### Reliability rules

- Avoid asserting on timing-sensitive exact timestamps.
- Poll for semantic states such as:
  - first output visible
  - additional output visible later
  - final marker visible
  - job status succeeded
- Prefer short polling loops with bounded timeouts over fixed sleeps.
- Capture the job state alongside browser observations for debugging.

## Suggested Task Breakdown

### Task 1: Unit coverage

- Add focused tests for notebook diffing, cell mutation helpers, and error handling.

### Task 2: Live integration coverage

- Add server-backed tests for insert, run, persistence, and diff behavior with no browser.

### Task 3: Browser open-tab coverage

- Add a browser test for inserting and running a new cell while the notebook is already open.

### Task 4: Browser streaming coverage

- Add a browser test that proves partial output appears before completion for a newly inserted long-running cell.

### Task 5: Browser late-open coverage

- Add a browser test that opens the notebook mid-execution and proves previously produced output is visible immediately, then later output continues to stream.

### Task 6: CI hardening

- Make server startup, notebook naming, workspace naming, and browser polling deterministic enough for parallel CI execution.

## Failure Criteria

Any of the following is a failure:

- inserted cell is visible but not executable
- execution works only when the notebook is closed
- execution works only after reopening or refreshing
- outputs appear only after completion, never during streaming
- late-open does not show already-produced output
- opening late restarts, duplicates, or otherwise changes execution
- SDK state and JupyterLab state disagree on outputs or execution count

## Notes From Live Validation

The current implementation has already shown these behaviors in manual validation:

- open-tab streaming: the browser observed new output while the job was still `running`
- late-open streaming: the browser showed already-produced output immediately on first render, then later output continued to appear

These observations should be converted into checked-in regression tests so the behavior stays stable over time.
