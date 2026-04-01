/**
 * JupyterLab Hypernote Status Extension
 *
 * Projects server state into the JupyterLab UI. Does NOT own state.
 *
 * Renders:
 * - Runtime status badge: live / detached / stopped
 * - Currently running job and actor
 * - Queued jobs by actor
 * - Awaiting-input indicator (non-modal)
 * - Per-cell attribution
 * - Resumable runtime indicator
 */

import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin,
} from '@jupyterlab/application';

import { INotebookTracker } from '@jupyterlab/notebook';

import { Widget } from '@lumino/widgets';

import { Poll } from '@lumino/polling';

import { HypernoteStatusWidget } from './status_widget';
import { HypernoteClient } from './client';

const PLUGIN_ID = 'jupyterlab-hypernote:plugin';

/**
 * Main extension plugin.
 */
const plugin: JupyterFrontEndPlugin<void> = {
  id: PLUGIN_ID,
  autoStart: true,
  requires: [INotebookTracker],
  activate: (app: JupyterFrontEnd, tracker: INotebookTracker) => {
    console.log('JupyterLab Hypernote extension activated');

    const client = new HypernoteClient(app.serviceManager.serverSettings);
    const statusWidget = new HypernoteStatusWidget(client);

    // Add status widget to the right sidebar
    app.shell.add(statusWidget, 'right', { rank: 1000 });

    // Poll for status updates when a notebook is active
    const poll = new Poll({
      auto: true,
      factory: async () => {
        const current = tracker.currentWidget;
        if (current) {
          const path = current.context.path;
          await statusWidget.updateStatus(path);
        }
      },
      frequency: { interval: 3000, backoff: true, max: 10000 },
      name: 'hypernote-status-poll',
    });

    // Refresh immediately when notebook changes
    tracker.currentChanged.connect(() => {
      void poll.refresh();
    });
  },
};

export default plugin;
