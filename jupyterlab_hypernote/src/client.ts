/**
 * HTTP client for Hypernote REST API.
 */

import { ServerConnection } from '@jupyterlab/services';

export interface RuntimeStatus {
  state: string;
  runtime_id: string | null;
  kernel_id: string | null;
  attached_clients: string[];
  last_activity: number;
}

export interface JobInfo {
  job_id: string;
  notebook_id: string;
  actor_id: string;
  actor_type: string;
  action: string;
  status: string;
  target_cells: string | null;
  created_at: number;
  started_at: number | null;
  completed_at: number | null;
}

export interface CellAttribution {
  last_editor_id: string | null;
  last_editor_type: string | null;
  last_executor_id: string | null;
  last_executor_type: string | null;
  updated_at: number;
}

export interface NotebookStatus {
  runtime: RuntimeStatus;
  jobs: JobInfo[];
}

export class HypernoteClient {
  private _settings: ServerConnection.ISettings;

  constructor(settings: ServerConnection.ISettings) {
    this._settings = settings;
  }

  private async _fetch(path: string): Promise<any> {
    const url = `${this._settings.baseUrl}hypernote/api${path}`;
    const response = await ServerConnection.makeRequest(
      url,
      { method: 'GET' },
      this._settings
    );
    if (!response.ok) {
      throw new Error(`Hypernote API error: ${response.status}`);
    }
    return response.json();
  }

  async getRuntimeStatus(notebookId: string): Promise<RuntimeStatus> {
    return this._fetch(`/notebooks/${encodeURIComponent(notebookId)}/runtime`);
  }

  async getJobs(notebookId: string): Promise<{ jobs: JobInfo[] }> {
    return this._fetch(`/jobs?notebook_id=${encodeURIComponent(notebookId)}`);
  }

  async getCellAttribution(
    notebookId: string,
    cellId: string
  ): Promise<CellAttribution> {
    return this._fetch(
      `/notebooks/${encodeURIComponent(notebookId)}/cells/${encodeURIComponent(cellId)}/attribution`
    );
  }

  async getNotebookStatus(notebookId: string): Promise<NotebookStatus> {
    const [runtime, jobsData] = await Promise.all([
      this.getRuntimeStatus(notebookId),
      this.getJobs(notebookId),
    ]);
    return { runtime, jobs: jobsData.jobs };
  }
}
