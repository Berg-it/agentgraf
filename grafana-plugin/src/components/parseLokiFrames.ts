import { DataFrame, FieldType } from '@grafana/data';
import { TraceSpan } from '../types';

/**
 * Parse Loki data frames into TraceSpan objects.
 *
 * Grafana passes us `props.data.series` — arrays of DataFrames.
 * Loki returns one frame per stream, with a "Time" field and a "Line" field.
 * The "Line" contains the JSON log line pushed by AgentGraf.
 */
export function parseLokiFrames(frames: DataFrame[]): TraceSpan[] {
  const spans: TraceSpan[] = [];

  for (const frame of frames) {
    // Find the Time and Line fields
    const lineField = frame.fields.find((f) => f.name === 'Line');
    if (!lineField || lineField.type !== FieldType.string) {
      continue;
    }

    for (let i = 0; i < lineField.values.length; i++) {
      const raw = lineField.values[i];
      if (!raw) continue;

      try {
        const span = JSON.parse(raw) as TraceSpan;
        spans.push(span);
      } catch {
        // skip malformed lines
      }
    }
  }

  return spans;
}
