"""Tests for agentgraf.models — TraceSpan, SpanKind, SpanStatus."""
import time

from agentgraf.models import SpanKind, SpanStatus, TraceSpan


class TestTraceSpanDefaults:
    """Verify that a fresh TraceSpan has sensible defaults."""

    def test_default_fields(self):
        span = TraceSpan(run_id="run-1", name="test-span")

        assert span.run_id == "run-1"
        assert span.name == "test-span"
        assert span.project == "default"
        assert span.kind == SpanKind.CHAIN
        assert span.status == SpanStatus.OK
        assert span.trace_id is not None and len(span.trace_id) == 32
        assert span.span_id is not None and len(span.span_id) == 16
        assert span.parent_span_id is None
        assert span.end_time is None
        assert span.latency_ms is None
        assert span.input_tokens == 0
        assert span.output_tokens == 0
        assert span.total_tokens == 0
        assert span.tags == {}
        assert span.metadata == {}
        assert span.agentgraf_version == 1

    def test_start_time_is_set(self):
        span = TraceSpan(run_id="r1", name="s1")
        assert span.start_time > 0
        assert span.start_time <= time.time()


class TestTraceSpanFinish:
    """Span life-cycle: finish() computes end_time and latency_ms."""

    def test_finish_basic(self):
        span = TraceSpan(run_id="r1", name="s1")
        span.finish()

        assert span.end_time is not None
        assert span.end_time >= span.start_time
        assert span.latency_ms is not None
        assert span.latency_ms >= 0

    def test_finish_with_error(self):
        span = TraceSpan(run_id="r1", name="s1")
        span.finish(status=SpanStatus.ERROR, error="something broke")

        assert span.status == SpanStatus.ERROR
        assert span.error == "something broke"
        assert span.end_time is not None

    def test_finish_with_custom_end_time(self):
        span = TraceSpan(run_id="r1", name="s1", start_time=1000.0)
        span.finish(end_time=2000.0)

        assert span.end_time == 2000.0
        assert span.latency_ms == 1_000_000  # 1000 seconds → ms


class TestTraceSpanHelpers:
    """Fluent helpers: set_tag, set_metadata, set_input, set_output, set_tokens."""

    def test_set_tag(self):
        span = TraceSpan(run_id="r1", name="s1")
        span.set_tag("env", "prod")

        assert span.tags == {"env": "prod"}

    def test_set_metadata(self):
        span = TraceSpan(run_id="r1", name="s1")
        span.set_metadata("version", 2)

        assert span.metadata == {"version": 2}

    def test_set_input_truncates(self):
        span = TraceSpan(run_id="r1", name="s1")
        long_text = "x" * 100
        span.set_input(long_text, truncate=50)

        assert len(span.input_data) == 50

    def test_set_input_no_truncation_needed(self):
        span = TraceSpan(run_id="r1", name="s1")
        span.set_input("hello", truncate=100)

        assert span.input_data == "hello"

    def test_set_output(self):
        span = TraceSpan(run_id="r1", name="s1")
        span.set_output("done!")

        assert span.output_data == "done!"

    def test_set_tokens(self):
        span = TraceSpan(run_id="r1", name="s1")
        span.set_tokens(input_tokens=100, output_tokens=50)

        assert span.input_tokens == 100
        assert span.output_tokens == 50
        assert span.total_tokens == 150


class TestTraceSpanSerialization:
    """to_json() and model_dump_json()."""

    def test_to_json_is_valid_json(self):
        import json

        span = TraceSpan(run_id="r1", name="s1", model="gpt-4o")
        span_string = span.to_json()

        # Must parse back without error
        data = json.loads(span_string)

        assert data["run_id"] == "r1"
        assert data["name"] == "s1"
        assert data["model"] == "gpt-4o"
        assert data["kind"] == "chain"

    def test_to_json_excludes_none(self):
        span = TraceSpan(run_id="r1", name="s1")
        span_string = span.to_json()

        # Fields with None (like parent_span_id, run_name) should not appear
        assert "parent_span_id" not in span_string
        assert "run_name" not in span_string


class TestSpanKindEnum:
    """SpanKind values are lowercase strings for Loki labels."""

    def test_kind_values(self):
        assert SpanKind.LLM.value == "llm"
        assert SpanKind.TOOL.value == "tool"
        assert SpanKind.CHAIN.value == "chain"
        assert SpanKind.AGENT.value == "agent"
        assert SpanKind.RETRIEVER.value == "retriever"


class TestSpanStatusEnum:
    """SpanStatus values."""

    def test_status_values(self):
        assert SpanStatus.OK.value == "ok"
        assert SpanStatus.ERROR.value == "error"
