/**
 * Hypernote Status Widget for JupyterLab sidebar.
 *
 * Projects server truth. Does NOT own state.
 *
 * Displays:
 * - Runtime badge: live / detached / stopped
 * - Currently running: {actor} executing cell {n}
 * - Queue: N jobs queued (actor-1, user-gilad)
 * - Awaiting input indicator (non-modal)
 * - Per-cell attribution: last edited by / last run by
 * - Resumable runtime indicator
 */

import { Widget } from '@lumino/widgets';

import { HypernoteClient, NotebookStatus, JobInfo } from './client';

const RUNTIME_BADGES: Record<string, { label: string; color: string }> = {
  'live-attached': { label: 'LIVE', color: '#22c55e' },
  'live-detached': { label: 'DETACHED', color: '#eab308' },
  'awaiting-input': { label: 'AWAITING INPUT', color: '#f97316' },
  stopped: { label: 'STOPPED', color: '#6b7280' },
  starting: { label: 'STARTING', color: '#3b82f6' },
  stopping: { label: 'STOPPING', color: '#6b7280' },
  failed: { label: 'FAILED', color: '#ef4444' },
};

export class HypernoteStatusWidget extends Widget {
  private _client: HypernoteClient;
  private _currentNotebook: string | null = null;

  constructor(client: HypernoteClient) {
    super();
    this._client = client;
    this.id = 'hypernote-status';
    this.title.label = 'Hypernote';
    this.title.closable = true;
    this.addClass('jp-HypernoteStatus');
    this._render(null);
  }

  async updateStatus(notebookPath: string): Promise<void> {
    this._currentNotebook = notebookPath;
    try {
      const status = await this._client.getNotebookStatus(notebookPath);
      this._render(status);
    } catch {
      this._renderError();
    }
  }

  private _render(status: NotebookStatus | null): void {
    if (!status) {
      this.node.innerHTML = `
        <div class="hn-status-panel">
          <div class="hn-section-title">Hypernote</div>
          <div class="hn-muted">No notebook selected</div>
        </div>
      `;
      return;
    }

    const runtime = status.runtime;
    const badge = RUNTIME_BADGES[runtime.state] || {
      label: runtime.state.toUpperCase(),
      color: '#6b7280',
    };

    const activeJobs = status.jobs.filter(
      (j: JobInfo) =>
        j.status === 'queued' || j.status === 'running' || j.status === 'awaiting_input'
    );
    const runningJobs = activeJobs.filter((j: JobInfo) => j.status === 'running');
    const queuedJobs = activeJobs.filter((j: JobInfo) => j.status === 'queued');
    const awaitingInput = activeJobs.filter(
      (j: JobInfo) => j.status === 'awaiting_input'
    );
    const recentJobs = status.jobs.slice(0, 10);

    this.node.innerHTML = `
      <div class="hn-status-panel">
        <div class="hn-section-title">Hypernote</div>

        <!-- Runtime Badge -->
        <div class="hn-runtime-badge" style="color: ${badge.color}">
          <span class="hn-dot" style="background: ${badge.color}"></span>
          Runtime: ${badge.label}
          ${runtime.kernel_id ? `<span class="hn-muted">(${runtime.kernel_id})</span>` : ''}
        </div>

        ${
          runtime.state === 'live-detached'
            ? `<div class="hn-resumable">
                <strong>Resumable runtime available</strong> — attach to continue
              </div>`
            : ''
        }

        <!-- Attached Clients -->
        ${
          runtime.attached_clients && runtime.attached_clients.length > 0
            ? `<div class="hn-clients">
                Clients: ${runtime.attached_clients.join(', ')}
              </div>`
            : ''
        }

        <!-- Currently Running -->
        ${
          runningJobs.length > 0
            ? `<div class="hn-section">
                <div class="hn-label">Running</div>
                ${runningJobs
                  .map(
                    (j: JobInfo) =>
                      `<div class="hn-job hn-running">
                        ${j.actor_id} executing ${j.target_cells || 'cells'}
                      </div>`
                  )
                  .join('')}
              </div>`
            : ''
        }

        <!-- Awaiting Input -->
        ${
          awaitingInput.length > 0
            ? `<div class="hn-section hn-awaiting">
                <div class="hn-label">Awaiting Input</div>
                ${awaitingInput
                  .map(
                    (j: JobInfo) =>
                      `<div class="hn-job">Job ${j.job_id} needs input (${j.actor_id})</div>`
                  )
                  .join('')}
              </div>`
            : ''
        }

        <!-- Queue -->
        ${
          queuedJobs.length > 0
            ? `<div class="hn-section">
                <div class="hn-label">Queued (${queuedJobs.length})</div>
                ${queuedJobs
                  .map(
                    (j: JobInfo) =>
                      `<div class="hn-job hn-queued">${j.actor_id}: ${j.action}</div>`
                  )
                  .join('')}
              </div>`
            : ''
        }

        <!-- Recent Activity -->
        ${
          recentJobs.length > 0
            ? `<div class="hn-section">
                <div class="hn-label">Recent Activity</div>
                ${recentJobs
                  .map(
                    (j: JobInfo) =>
                      `<div class="hn-job hn-${j.status}">
                        <span class="hn-actor">${j.actor_id}</span>
                        ${j.action}
                        <span class="hn-status-tag">${j.status}</span>
                      </div>`
                  )
                  .join('')}
              </div>`
            : '<div class="hn-muted">No recent activity</div>'
        }
      </div>
    `;
  }

  private _renderError(): void {
    this.node.innerHTML = `
      <div class="hn-status-panel">
        <div class="hn-section-title">Hypernote</div>
        <div class="hn-error">Unable to reach Hypernote API</div>
      </div>
    `;
  }
}
