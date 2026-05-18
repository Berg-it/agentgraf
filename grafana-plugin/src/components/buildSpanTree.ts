import { TraceSpan, SpanNode } from '../types';

/**
 * Build a nested span tree from a flat list of spans.
 *
 * Uses span_id / parent_span_id to reconstruct the hierarchy.
 * Spans without a parent become root nodes.
 * Spans whose parent is missing become roots too (resilience).
 */
export function buildSpanTree(spans: TraceSpan[]): SpanNode[] {
  // Index all spans by span_id for fast lookup
  const byId: Map<string, SpanNode> = new Map();

  // Create SpanNodes (with children array + depth)
  const nodes: SpanNode[] = spans.map((span) => {
    const node: SpanNode = { ...span, children: [], depth: 0 };
    byId.set(span.span_id, node);
    return node;
  });

  const roots: SpanNode[] = [];

  // Attach children to parents
  for (const node of nodes) {
    if (node.parent_span_id) {
      const parent = byId.get(node.parent_span_id);
      if (parent) {
        parent.children.push(node);
        continue;
      }
      // parent not found — treat as root
    }
    roots.push(node);
  }

  // Set depths recursively
  function setDepth(node: SpanNode, depth: number): void {
    node.depth = depth;
    for (const child of node.children) {
      setDepth(child, depth + 1);
    }
  }

  for (const root of roots) {
    setDepth(root, 0);
  }

  return roots;
}
