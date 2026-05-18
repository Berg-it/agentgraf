/** TypeScript mirrors of agentgraf TraceSpan — kept simple and explicit. */

export type SpanKind = 'llm' | 'tool' | 'chain' | 'agent' | 'retriever';
export type SpanStatus = 'ok' | 'error';

export interface TraceSpan {
  trace_id: string;           // 32 hex chars
  span_id: string;            // 16 hex chars
  parent_span_id: string | null;
  run_id: string;
  run_name: string | null;
  project: string;
  kind: SpanKind;
  name: string;
  start_time: number;         // unix seconds
  end_time: number | null;
  latency_ms: number | null;
  model: string | null;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  input_data: string | null;
  output_data: string | null;
  status: SpanStatus;
  error: string | null;
  tags: Record<string, unknown>;
  metadata: Record<string, unknown>;
  agentgraf_version: number;
}

/** A span with its children resolved for tree rendering. */
export interface SpanNode extends TraceSpan {
  children: SpanNode[];
  depth: number;
}
