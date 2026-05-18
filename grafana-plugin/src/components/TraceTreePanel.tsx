import React, { useMemo, useState } from 'react';

import { TraceSpan, SpanNode } from '../types';
import { buildSpanTree } from './buildSpanTree';

interface Props {
  spans: TraceSpan[];
}

// -- color per span kind ---------------------------------------------------
const KIND_COLORS: Record<string, string> = {
  llm: '#805ad5',
  tool: '#dd6b20',
  chain: '#3182ce',
  agent: '#38a169',
  retriever: '#d69e2e',
};
const DEFAULT_COLOR = '#718096';

function kindColor(kind: string): string {
  return KIND_COLORS[kind] || DEFAULT_COLOR;
}

// -- icon per span kind ---------------------------------------------------
const KIND_ICONS: Record<string, string> = {
  llm: '\u{1F916}',        // 🤖 robot
  tool: '\u{1F527}',       // 🔧 wrench
  chain: '\u{1F517}',      // 🔗 link
  agent: '\u{1F9E0}',      // 🧠 brain
  retriever: '\u{1F50D}',  // 🔍 magnifying glass
};
const DEFAULT_ICON = '\u{25CF}'; // ●

function kindIcon(kind: string): string {
  return KIND_ICONS[kind] || DEFAULT_ICON;
}

// -- ms formatter ----------------------------------------------------------
function fmtMs(ms: number | null): string {
  if (ms == null) return '-';
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${ms}ms`;
}

// ==========================================================================
//  Waterfall Trace Panel
// ==========================================================================

interface WaterfallRow {
  node: SpanNode;
  depth: number;
}

function flattenChronological(roots: SpanNode[]): WaterfallRow[] {
  const result: WaterfallRow[] = [];

  function walk(node: SpanNode, depth: number): void {
    result.push({ node, depth });
    const sorted = [...node.children].sort((a, b) => a.start_time - b.start_time);
    for (const child of sorted) {
      walk(child, depth + 1);
    }
  }

  const sortedRoots = [...roots].sort((a, b) => a.start_time - b.start_time);
  for (const root of sortedRoots) {
    walk(root, 0);
  }

  return result;
}

export const TraceTreePanel: React.FC<Props> = ({ spans }) => {
  const roots: SpanNode[] = useMemo(() => buildSpanTree(spans), [spans]);
  const rows: WaterfallRow[] = useMemo(() => flattenChronological(roots), [roots]);

  const { minTime, maxTime, totalMs } = useMemo(() => {
    let min = Infinity;
    let max = -Infinity;
    for (const span of spans) {
      if (span.start_time < min) min = span.start_time;
      const end = span.end_time ?? span.start_time;
      if (end > max) max = end;
    }
    if (min === Infinity) return { minTime: 0, maxTime: 0, totalMs: 0 };
    return { minTime: min, maxTime: max, totalMs: (max - min) * 1000 };
  }, [spans]);

  // Track which spans are expanded
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toggle = (spanId: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(spanId)) {
        next.delete(spanId);
      } else {
        next.add(spanId);
      }
      return next;
    });
  };

  if (rows.length === 0) {
    return <div style={{ padding: 16, color: '#888' }}>No traces found.</div>;
  }

  const labelWidth = 280;

  return (
    <div style={{ fontFamily: 'monospace', fontSize: 13, color: '#e2e8f0' }}>
      <WaterfallAxis minTime={minTime} maxTime={maxTime} totalMs={totalMs} labelWidth={labelWidth} />

      {rows.map((row, i) => (
        <React.Fragment key={row.node.span_id}>
          <WaterfallRowComponent
            row={row}
            minTime={minTime}
            maxTime={maxTime}
            totalMs={totalMs}
            labelWidth={labelWidth}
            isLast={false}
            isExpanded={expanded.has(row.node.span_id)}
            onToggle={() => toggle(row.node.span_id)}
          />
          {expanded.has(row.node.span_id) && (
            <DetailRow row={row} labelWidth={labelWidth} />
          )}
        </React.Fragment>
      ))}
    </div>
  );
};

// ==========================================================================
//  Time Axis
// ==========================================================================

interface AxisProps {
  minTime: number;
  maxTime: number;
  totalMs: number;
  labelWidth: number;
}

const WaterfallAxis: React.FC<AxisProps> = ({ totalMs, labelWidth }) => {
  if (totalMs === 0) return null;

  const ticks: { pct: number; label: string }[] = [];
  for (let i = 0; i <= 4; i++) {
    const pct = i / 4;
    ticks.push({ pct: pct * 100, label: fmtMs(Math.round(pct * totalMs)) });
  }

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        height: 28,
        borderBottom: '1px solid #444',
        fontSize: 11,
        color: '#666',
        position: 'relative',
      }}
    >
      <div style={{ width: labelWidth, flexShrink: 0 }} />
      <div style={{ flex: 1, position: 'relative', height: '100%' }}>
        {ticks.map((tick) => (
          <div
            key={tick.pct}
            style={{
              position: 'absolute',
              left: `${tick.pct}%`,
              top: 0,
              transform: 'translateX(-50%)',
            }}
          >
            {tick.label}
          </div>
        ))}
      </div>
    </div>
  );
};

// ==========================================================================
//  Row
// ==========================================================================

interface RowProps {
  row: WaterfallRow;
  minTime: number;
  maxTime: number;
  totalMs: number;
  labelWidth: number;
  isLast: boolean;
  isExpanded: boolean;
  onToggle: () => void;
}

const WaterfallRowComponent: React.FC<RowProps> = ({
  row,
  minTime,
  maxTime,
  totalMs,
  labelWidth,
  isExpanded,
  onToggle,
}) => {
  const { node, depth } = row;
  const indent = depth * 16;
  const color = kindColor(node.kind);
  const isError = node.status === 'error';
  const durationMs = node.latency_ms ?? 0;

  const leftPct = totalMs > 0 ? ((node.start_time - minTime) / (maxTime - minTime)) * 100 : 0;
  const widthPct = totalMs > 0 ? (durationMs / totalMs) * 100 : 0;
  const minWidthPct = 0.3;

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        height: 30,
        borderBottom: '1px solid #1a2a3e',
        opacity: isError ? 0.7 : 1,
        cursor: 'pointer',
      }}
      onClick={onToggle}
    >
      {/* ---- Label (left side) ---- */}
      <div
        style={{
          width: labelWidth,
          flexShrink: 0,
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          paddingLeft: 8 + indent,
          overflow: 'hidden',
        }}
      >
        {/* Expand arrow */}
        <span style={{ fontSize: 10, color: '#666', width: 10, flexShrink: 0 }}>
          {isExpanded ? '▼' : '▶'}
        </span>

        <span style={{ color, fontSize: 14, lineHeight: 1, flexShrink: 0 }}>•</span>

        {/* Kind icon */}
        <span style={{ fontSize: 12, flexShrink: 0 }} title={node.kind}>
          {kindIcon(node.kind)}
        </span>

        <span
          style={{
            flex: 1,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            color: isError ? '#fc8181' : '#e2e8f0',
          }}
        >
          {node.name}
        </span>

        {node.total_tokens > 0 && (
          <span style={{ fontSize: 10, color: '#666', flexShrink: 0 }}>
            {node.total_tokens}tok
          </span>
        )}
      </div>

      {/* ---- Timeline bar area ---- */}
      <div style={{ flex: 1, position: 'relative', height: '100%', display: 'flex', alignItems: 'center' }}>
        {[0, 25, 50, 75, 100].map((pct) => (
          <div
            key={pct}
            style={{
              position: 'absolute',
              left: `${pct}%`,
              top: 0,
              bottom: 0,
              width: 1,
              backgroundColor: '#1a2a3e',
            }}
          />
        ))}

        {totalMs > 0 && durationMs > 0 && (
          <div
            style={{
              position: 'absolute',
              left: `${leftPct}%`,
              width: `${Math.max(widthPct, minWidthPct)}%`,
              height: 14,
              borderRadius: 7,
              backgroundColor: isError ? '#fc8181' : color,
              minWidth: 3,
              opacity: 0.85,
            }}
          />
        )}

        <span
          style={{
            position: 'absolute',
            left: `${Math.min(leftPct + Math.max(widthPct, minWidthPct) + 0.5, 99)}%`,
            fontSize: 10,
            color: '#888',
          }}
        >
          {fmtMs(durationMs)}
        </span>
      </div>
    </div>
  );
};

// ==========================================================================
//  Detail (input / output)
// ==========================================================================

interface DetailProps {
  row: WaterfallRow;
  labelWidth: number;
}

const DetailRow: React.FC<DetailProps> = ({ row, labelWidth }) => {
  const { node, depth } = row;
  const indent = depth * 16;

  return (
    <div
      style={{
        borderBottom: '1px solid #2d3748',
        backgroundColor: '#0d1117',
      }}
    >
      {/* Input */}
      <div
        style={{
          display: 'flex',
          padding: '6px 0',
          fontSize: 12,
        }}
      >
        <div
          style={{
            width: labelWidth,
            flexShrink: 0,
            paddingLeft: 28 + indent,
            color: '#666',
            textTransform: 'uppercase',
            fontSize: 10,
          }}
        >
          Input
        </div>
        <pre
          style={{
            flex: 1,
            margin: 0,
            padding: '2px 12px',
            color: '#a0aec0',
            overflow: 'auto',
            maxHeight: 300,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
            fontSize: 11,
          }}
        >
          {node.input_data || '(none)'}
        </pre>
      </div>

      {/* Output */}
      <div
        style={{
          display: 'flex',
          padding: '6px 0 8px',
          fontSize: 12,
        }}
      >
        <div
          style={{
            width: labelWidth,
            flexShrink: 0,
            paddingLeft: 28 + indent,
            color: '#666',
            textTransform: 'uppercase',
            fontSize: 10,
          }}
        >
          Output
        </div>
        <pre
          style={{
            flex: 1,
            margin: 0,
            padding: '2px 12px',
            color: '#a0aec0',
            overflow: 'auto',
            maxHeight: 300,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
            fontSize: 11,
          }}
        >
          {node.output_data || '(none)'}
        </pre>
      </div>
    </div>
  );
};
