import { PanelPlugin } from '@grafana/data';
import { RunsPanel } from './components/RunsPanel';

/**
 * AgentGraf panel plugin — plug it into any dashboard, configure a Loki query,
 * and see your AI agent traces.
 */
export const plugin = new PanelPlugin(RunsPanel)
  .setPanelOptions((builder) => {
    return builder
      .addSelect({
        path: 'viewMode',
        name: 'View mode',
        description: 'How to display the trace data',
        defaultValue: 'runs',
        settings: {
          options: [
            { label: 'Runs list', value: 'runs' },
            { label: 'Trace tree', value: 'trace' },
          ],
        },
      })
      .addTextInput({
        path: 'defaultProject',
        name: 'Default project',
        description: 'Show only traces from this project (leave empty for all)',
        defaultValue: '',
      });
  });
