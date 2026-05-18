"""
Example: send a few fake spans to Loki to verify the pipeline.

Usage:
    python examples/send_test_spans.py

Requires a Loki instance accessible at LOKI_URL (default: http://localhost:3100).
Set the env var if your Loki is elsewhere:
    LOKI_URL=http://loki.monitoring:3100 python examples/send_test_spans.py
"""
import os
import time

from agentgraf.models import SpanKind, TraceSpan
from agentgraf.client import LokiClient
from agentgraf.processor import BatchSpanProcessor

LOKI_URL = os.getenv("LOKI_URL", "http://localhost:3100/loki/api/v1/push")

print(f"→ Pushing test spans to {LOKI_URL} ...")

client = LokiClient(loki_url=LOKI_URL)
processor = BatchSpanProcessor(exporter=client.send_spans_sync, schedule_delay=1.0)
processor.start()

# Simulate a simple chain: chain → llm → tool → llm
trace_id = "aabbccddeeff00112233445566778899"

# Chain root
root = TraceSpan(
    trace_id=trace_id,
    span_id="chain000000000001",
    parent_span_id=None,
    run_id="test-run-001",
    run_name="test-invoke",
    project="demo",
    kind=SpanKind.CHAIN,
    name="my-agent",
    start_time=time.time(),
)
root.finish(end_time=root.start_time + 3.0)
processor.add_span(root)

# LLM call 1
llm1 = TraceSpan(
    trace_id=trace_id,
    span_id="llm00000000000001",
    parent_span_id="chain000000000001",
    run_id="test-run-001",
    project="demo",
    kind=SpanKind.LLM,
    name="gpt-4o",
    model="gpt-4o",
    start_time=root.start_time + 0.1,
)
llm1.finish(end_time=llm1.start_time + 1.5)
llm1.set_tokens(input_tokens=120, output_tokens=80)
processor.add_span(llm1)

# Tool call
tool = TraceSpan(
    trace_id=trace_id,
    span_id="tool0000000000001",
    parent_span_id="chain000000000001",
    run_id="test-run-001",
    project="demo",
    kind=SpanKind.TOOL,
    name="search_web",
    start_time=llm1.end_time + 0.1,
)
tool.finish(end_time=tool.start_time + 0.5)
processor.add_span(tool)

# LLM call 2
llm2 = TraceSpan(
    trace_id=trace_id,
    span_id="llm00000000000002",
    parent_span_id="chain000000000001",
    run_id="test-run-001",
    project="demo",
    kind=SpanKind.LLM,
    name="gpt-4o",
    model="gpt-4o",
    start_time=tool.end_time + 0.1,
)
llm2.finish(end_time=llm2.start_time + 2.0)
llm2.set_tokens(input_tokens=200, output_tokens=150)
processor.add_span(llm2)

print("→ Spans queued, waiting for flush ...")
time.sleep(2)  # let the background thread flush
processor.shutdown()
client.close_sync()

print("→ Done. Check Grafana → Explore → Loki → {project=\"demo\", job=\"agentgraf\"}")
