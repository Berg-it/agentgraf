import React, { useMemo } from 'react';
import { PanelProps } from '@grafana/data';

import { TraceSpan } from '../types';
import { parseLokiFrames } from './parseLokiFrames';
import { TraceTreePanel } from './TraceTreePanel';

interface AgentGrafOptions {
  viewMode: 'runs' | 'trace';
  defaultProject: string;
  selectedTraceId: string;
}

/** Green → yellow → red based on how close to max duration. */
function durationColor(ratio: number): string {
  if (ratio < 0.33) return '#48bb78'; // green — fast
  if (ratio < 0.66) return '#ecc94b'; // yellow — moderate
  return '#fc8181'; // red — slow
}

/**
 * Main panel component — entry point registered in module.ts.
 * Grafana calls this with `props.data.series` (Loki data frames).
 */
export const RunsPanel: React.FC<PanelProps<AgentGrafOptions>> = (props) => {
  const { data, options, width, height } = props;

  const spans: TraceSpan[] = useMemo(() => {
    if (!data?.series) return [];
    return parseLokiFrames(data.series);
  }, [data]);

  const filtered = useMemo(() => {
    const project = options.defaultProject;
    if (!project) return spans;
    return spans.filter((s) => s.project === project);
  }, [spans, options.defaultProject]);

  // Group spans by trace_id — one trace = one agent run.
  // trace_id is shared by all spans in the same invocation.
  const runs = useMemo(() => {
    const map = new Map<string, TraceSpan[]>();
    for (const span of filtered) {
      const list = map.get(span.trace_id) || [];
      list.push(span);
      map.set(span.trace_id, list);
    }
    return Array.from(map.entries())
      .map(([traceId, runSpans]) => {
        const errors = runSpans.filter((s) => s.status === 'error').length;
        const rootSpan = runSpans.find((s) => !s.parent_span_id) || runSpans[0];
        const inputTokens = runSpans.reduce((a, s) => a + s.input_tokens, 0);
        const outputTokens = runSpans.reduce((a, s) => a + s.output_tokens, 0);

        // Real wall-clock duration: rootSpan.end_time - rootSpan.start_time
        const realMs =
          rootSpan?.end_time != null
            ? (rootSpan.end_time - rootSpan.start_time) * 1000
            : 0;

        return {
          traceId,
          name: rootSpan?.run_name || rootSpan?.name || traceId.slice(0, 8),
          project: rootSpan?.project || '',
          count: runSpans.length,
          errors,
          totalMs: Math.round(realMs),
          inputTokens,
          outputTokens,
          startTime: rootSpan?.start_time || 0,
          endTime: rootSpan?.end_time || 0,
        };
      })
      .sort((a, b) => b.startTime - a.startTime);
  }, [filtered]);

  // Max duration across all runs (for relative bar sizing)
  const maxMs = useMemo(
    () => runs.reduce((m, r) => Math.max(m, r.totalMs), 0),
    [runs]
  );

  // KPIs computed from spans
  const kpis = useMemo(() => {
    const totalSpans = filtered.length;
    const totalRuns = runs.length;
    const errors = filtered.filter((s) => s.status === 'error').length;
    const allLatencies = filtered
      .map((s) => s.latency_ms)
      .filter((v): v is number => v != null);
    const avgLatency = allLatencies.length > 0
      ? allLatencies.reduce((a, b) => a + b, 0) / allLatencies.length
      : 0;
    const totalTokens = filtered.reduce((a, s) => a + s.total_tokens, 0);

    return { totalRuns, totalSpans, errors, avgLatency, totalTokens };
  }, [filtered, runs]);

  // ---- Render ----
  if (!data?.series || data.series.length === 0) {
    return (
      <div style={{ padding: 16, color: '#888' }}>
        No Loki data. Configure a Loki query that returns AgentGraf spans.
      </div>
    );
  }

  if (options.viewMode === 'trace') {
    const traceSpans = filtered.filter((s) => s.trace_id === options.selectedTraceId);
    return (
      <div style={{ width, height, overflow: 'auto' }}>
        <button
          onClick={() => props.onOptionsChange({ ...options, viewMode: 'runs' })}
          style={{
            margin: 8,
            padding: '4px 12px',
            background: 'none',
            border: '1px solid #555',
            borderRadius: 4,
            color: '#aaa',
            cursor: 'pointer',
            fontSize: 13,
            fontFamily: 'monospace',
          }}
        >
          ← Back to Runs
        </button>
        <TraceTreePanel spans={traceSpans} />
      </div>
    );
  }

  // Runs list view with KPIs + duration bars
  return (
    <div style={{ width, height, overflow: 'auto', fontSize: 13, fontFamily: 'monospace' }}>
      {/* ---- KPI bar ---- */}
      <div style={{
        display: 'flex',
        gap: 16,
        padding: '8px 12px',
        borderBottom: '1px solid #444',
        fontSize: 12,
        color: '#aaa',
      }}>
        <span>Runs: <b style={{ color: '#e2e8f0' }}>{kpis.totalRuns}</b></span>
        <span>Spans: <b style={{ color: '#e2e8f0' }}>{kpis.totalSpans}</b></span>
        <span>Avg: <b style={{ color: '#e2e8f0' }}>{Math.round(kpis.avgLatency)}ms</b></span>
        <span>Errors: <b style={{ color: kpis.errors > 0 ? '#fc8181' : '#e2e8f0' }}>{kpis.errors}</b></span>
        {kpis.totalTokens > 0 && (
          <span>Tokens: <b style={{ color: '#e2e8f0' }}>{kpis.totalTokens}</b></span>
        )}
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ borderBottom: '1px solid #444', textAlign: 'left', color: '#aaa' }}>
            <th style={{ padding: '6px 8px' }}>Run</th>
            <th style={{ padding: '6px 8px', width: 80 }}>Project</th>
            <th style={{ padding: '6px 8px', textAlign: 'right', width: 45 }}>Spans</th>
            <th style={{ padding: '6px 8px', textAlign: 'right', width: 45 }}>Err</th>
            <th style={{ padding: '6px 8px', width: 120 }}>Duration</th>
            <th style={{ padding: '6px 8px', textAlign: 'right', width: 85 }}>Tokens I/O</th>
            <th style={{ padding: '6px 8px', textAlign: 'right', width: 60 }}>Total</th>
            <th style={{ padding: '6px 8px', width: 80 }}>Time</th>
          </tr>
        </thead>
        <tbody>
          {runs.length === 0 ? (
            <tr>
              <td colSpan={8} style={{ padding: 16, color: '#888' }}>
                No spans found.
              </td>
            </tr>
          ) : (
            runs.map((run) => {
              const ratio = maxMs > 0 ? run.totalMs / maxMs : 0;
              const barW = Math.max(ratio * 110, 2); // 110px max bar, min 2px
              const color = durationColor(ratio);
              return (
                <tr
                  key={run.traceId}
                  style={{ borderBottom: '1px solid #333', cursor: 'pointer' }}
                  onClick={() => {
                    props.onOptionsChange({
                      ...options,
                      viewMode: 'trace',
                      selectedTraceId: run.traceId,
                    });
                  }}
                >
                  <td style={{ padding: '6px 8px' }}>{run.name}</td>
                  <td style={{ padding: '6px 8px', fontSize: 11, color: '#aaa' }}>{run.project}</td>
                  <td style={{ padding: '6px 8px', textAlign: 'right' }}>{run.count}</td>
                  <td
                    style={{
                      padding: '6px 8px',
                      textAlign: 'right',
                      color: run.errors > 0 ? '#e53e3e' : '#888',
                    }}
                  >
                    {run.errors > 0 ? run.errors : '-'}
                  </td>
                  <td style={{ padding: '6px 8px', verticalAlign: 'middle' }}>
                    <div
                      style={{
                        height: 8,
                        borderRadius: 4,
                        backgroundColor: color,
                        width: barW,
                        minWidth: 2,
                        transition: 'width 0.2s',
                      }}
                    />
                  </td>
                  <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                    {run.inputTokens > 0 || run.outputTokens > 0
                      ? `${run.inputTokens}/${run.outputTokens}`
                      : '-'}
                  </td>
                  <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                    {run.totalMs >= 1000
                      ? `${(run.totalMs / 1000).toFixed(1)}s`
                      : `${run.totalMs}ms`}
                  </td>
                  <td style={{ padding: '6px 8px', fontSize: 11, color: '#888' }}>
                    {run.startTime
                      ? new Date(run.startTime * 1000).toLocaleTimeString()
                      : '-'}
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
};
