"""Tests for agentgraf.client — LokiClient and _build_loki_payload."""
import pytest
import respx
from httpx import Response

from agentgraf.client import LokiClient, _build_loki_payload
from agentgraf.models import SpanKind, SpanStatus, TraceSpan


# -- helpers -----------------------------------------------------------

def _make_span(name="test", kind=SpanKind.LLM, project="test-proj", start_time=1000.0):
    """Quick factory for a finished span."""
    span = TraceSpan(run_id="run-1", name=name, kind=kind, project=project,
                     start_time=start_time)
    span.finish(end_time=start_time + 0.5)
    return span


# -- _build_loki_payload -----------------------------------------------

class TestBuildLokiPayload:
    """Internal helper that builds the Loki push API body."""

    def test_single_span_structure(self):
        span = _make_span(name="gpt-4o", kind=SpanKind.LLM, project="k-fix")
        payload = _build_loki_payload([span])

        assert "streams" in payload
        assert len(payload["streams"]) == 1

        stream = payload["streams"][0]
        assert stream["stream"]["job"] == "agentgraf"
        assert stream["stream"]["project"] == "k-fix"
        assert stream["stream"]["kind"] == "llm"

        assert len(stream["values"]) == 1
        ts_ns, line, meta = stream["values"][0]

        # timestamp must be string nanoseconds
        assert isinstance(ts_ns, str)
        assert ts_ns == "1000000000000"  # 1000.0 * 1e9

        # line is valid JSON
        import json
        data = json.loads(line)
        assert data["name"] == "gpt-4o"
        assert data["kind"] == "llm"

        # structured metadata
        assert meta["trace_id"] == span.trace_id
        assert meta["span_id"] == span.span_id
        assert meta["run_id"] == span.run_id

    def test_groups_by_project_and_kind(self):
        span_a = _make_span(name="gpt-4o", kind=SpanKind.LLM, project="p1")
        span_b = _make_span(name="gpt-4o", kind=SpanKind.LLM, project="p1")
        span_c = _make_span(name="search", kind=SpanKind.TOOL, project="p1")
        span_d = _make_span(name="gpt-4o", kind=SpanKind.LLM, project="p2")

        payload = _build_loki_payload([span_a, span_b, span_c, span_d])

        # 3 unique groups: (p1, llm), (p1, tool), (p2, llm)
        assert len(payload["streams"]) == 3

    def test_multiple_values_per_stream(self):
        spans = [_make_span(name=f"span-{i}", kind=SpanKind.CHAIN, project="p")
                 for i in range(3)]
        payload = _build_loki_payload(spans)

        stream = payload["streams"][0]
        assert len(stream["values"]) == 3

    def test_parent_span_id_in_metadata(self):
        span = _make_span(name="child")
        span.parent_span_id = "abcdef0123456789"

        payload = _build_loki_payload([span])
        _ts, _line, meta = payload["streams"][0]["values"][0]

        assert meta["parent_span_id"] == "abcdef0123456789"


# -- LokiClient.send_spans_sync ----------------------------------------

class TestLokiClientSync:
    """Tests for send_spans_sync using respx mock."""

    LOKI_URL = "http://fake-loki:3100/loki/api/v1/push"

    def test_successful_push(self):
        span = _make_span()
        with respx.mock:
            respx.post(self.LOKI_URL).respond(204)
            client = LokiClient(loki_url=self.LOKI_URL)
            ok = client.send_spans_sync([span])

        assert ok is True

    def test_retry_on_500_then_succeed(self):
        span = _make_span()
        with respx.mock:
            route = respx.post(self.LOKI_URL)
            route.side_effect = [Response(500), Response(204)]

            client = LokiClient(loki_url=self.LOKI_URL, max_retries=2, retry_delay=0.01)
            ok = client.send_spans_sync([span])

        assert ok is True
        assert route.call_count == 2

    def test_all_retries_exhausted(self):
        span = _make_span()
        with respx.mock:
            route = respx.post(self.LOKI_URL).respond(503)

            client = LokiClient(loki_url=self.LOKI_URL, max_retries=3, retry_delay=0.01)
            ok = client.send_spans_sync([span])

        assert ok is False
        assert route.call_count == 3

    def test_400_bad_request_no_retry(self):
        """400 is a client error — returns immediately, no retry."""
        span = _make_span()
        with respx.mock:
            route = respx.post(self.LOKI_URL).respond(400)

            client = LokiClient(loki_url=self.LOKI_URL, max_retries=2, retry_delay=0.01)
            ok = client.send_spans_sync([span])

        assert ok is False
        assert route.call_count == 1  # no retry on 4xx


class TestLokiClientLifecycle:
    """close_sync / close_async."""

    def test_close_sync_clears_client(self):
        client = LokiClient(loki_url="http://lok:3100/loki/api/v1/push")
        client.close_sync()
        assert client._sync_client is None

    def test_close_sync_before_any_send(self):
        client = LokiClient(loki_url="http://lok:3100/loki/api/v1/push")
        client.close_sync()
        assert client._sync_client is None
