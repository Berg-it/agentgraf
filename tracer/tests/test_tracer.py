"""Tests for agentgraf.tracer — AgentGrafTracer (LangChain callback)."""
import time
import uuid
from unittest.mock import MagicMock

from agentgraf.models import SpanKind, SpanStatus, TraceSpan
from agentgraf.processor import BatchSpanProcessor
from agentgraf.tracer import AgentGrafTracer, _to16, _model_name


# -- helpers -----------------------------------------------------------

def _make_capturer():
    """Create a processor that captures spans in a list."""
    spans = []
    def capture(batch):
        spans.extend(batch)
        return True
    processor = BatchSpanProcessor(exporter=capture, schedule_delay=0.01)
    processor.start()
    return processor, spans


def _mock_llm_serialized(model="gpt-4o"):
    return {"kwargs": {"model_name": model}, "id": ["ChatOpenAI"]}


def _mock_chain_serialized(name="my-chain"):
    return {"name": name, "id": ["RunnableSequence"]}


def _mock_tool_serialized(name="search"):
    return {"name": name}


# -- _to16 and _model_name ---------------------------------------------

class TestHelpers:
    def test_to16_converts_uuid(self):
        uid = str(uuid.uuid4())
        result = _to16(uid)
        assert len(result) == 16
        assert result == uid.replace("-", "")[:16]

    def test_to16_short_string(self):
        assert len(_to16("abc")) == 3

    def test_model_name_from_kwargs(self):
        assert _model_name({"kwargs": {"model_name": "gpt-4o"}}) == "gpt-4o"

    def test_model_name_fallback_to_id(self):
        assert _model_name({"kwargs": {}, "id": ["SomeLLM"]}) == "SomeLLM"

    def test_model_name_unknown(self):
        assert _model_name({"kwargs": {}}) == "unknown"


# -- LLM hooks ----------------------------------------------------------

class TestLLMHooks:
    def test_on_llm_start_creates_pending_span(self):
        proc, _spans = _make_capturer()
        tracer = AgentGrafTracer(processor=proc, project="test")

        run_id = str(uuid.uuid4())
        tracer.on_llm_start(
            _mock_llm_serialized("gpt-4o"),
            prompts=["hello"],
            run_id=run_id,
            tags=["invoke"],
            metadata={"env": "dev"},
        )

        assert run_id in tracer._pending
        span = tracer._pending[run_id]
        assert span.name == "gpt-4o"
        assert span.model == "gpt-4o"
        assert span.kind == SpanKind.LLM
        assert span.project == "test"
        assert span.run_id == run_id
        assert span.run_name == "invoke"
        assert span.input_data is not None
        assert span.metadata == {"env": "dev"}
        assert len(span.trace_id) == 32
        assert len(span.span_id) == 16
        proc.shutdown()

    def test_on_llm_end_finishes_and_pushes(self):
        proc, spans = _make_capturer()
        tracer = AgentGrafTracer(processor=proc, project="test")

        tracer.on_llm_start(_mock_llm_serialized("gpt-4o"), prompts=["hi"], run_id="run-2")

        # Mock LLM response with usage_metadata
        # NOTE: response.generations is List[List[Generation]] in LangChain
        resp = MagicMock()
        gen = MagicMock()
        gen.message = MagicMock()
        gen.message.usage_metadata = {"input_tokens": 20, "output_tokens": 10}
        gen.message.content = "hello back"
        resp.generations = [[gen]]  # nested list

        tracer.on_llm_end(resp, run_id="run-2")

        proc.force_flush()
        proc.shutdown()

        assert len(spans) == 1
        s = spans[0]
        assert s.status == SpanStatus.OK
        assert s.end_time is not None
        assert s.latency_ms is not None and s.latency_ms >= 0
        assert s.input_tokens == 20
        assert s.output_tokens == 10
        assert s.total_tokens == 30
        assert "hello back" in (s.output_data or "")

    def test_on_llm_end_without_start_no_push(self):
        proc, spans = _make_capturer()
        tracer = AgentGrafTracer(processor=proc, project="test")

        resp = MagicMock()
        resp.generations = []
        resp.llm_output = {}
        tracer.on_llm_end(resp, run_id="never-started")

        proc.force_flush()
        proc.shutdown()
        assert len(spans) == 0

    def test_on_llm_error_sets_error_status(self):
        proc, spans = _make_capturer()
        tracer = AgentGrafTracer(processor=proc, project="test")

        tracer.on_llm_start(_mock_llm_serialized(), prompts=["x"], run_id="run-err")
        tracer.on_llm_error(ValueError("api down"), run_id="run-err")

        proc.force_flush()
        proc.shutdown()

        assert len(spans) == 1
        s = spans[0]
        assert s.status == SpanStatus.ERROR
        assert "api down" in (s.error or "")

    def test_fill_tokens_llm_output_format(self):
        """Older LangChain: token_usage in llm_output."""
        proc, spans = _make_capturer()
        tracer = AgentGrafTracer(processor=proc, project="test")

        tracer.on_llm_start(_mock_llm_serialized(), prompts=["x"], run_id="run-old")

        resp = MagicMock()
        resp.generations = []
        resp.llm_output = {"token_usage": {"prompt_tokens": 500, "completion_tokens": 300}}

        tracer.on_llm_end(resp, run_id="run-old")

        proc.force_flush()
        proc.shutdown()

        assert spans[0].input_tokens == 500
        assert spans[0].output_tokens == 300
        assert spans[0].total_tokens == 800


# -- Trace ID inheritance -----------------------------------------------

class TestTraceIDInheritance:
    def test_parent_to_child_propagation(self):
        """A child span inherits trace_id from its parent."""
        proc, spans = _make_capturer()
        tracer = AgentGrafTracer(processor=proc, project="test")

        # Root chain
        tracer.on_chain_start(_mock_chain_serialized("root"), {}, run_id="root-id")
        root_trace_id = tracer._pending["root-id"].trace_id

        # Child LLM
        tracer.on_llm_start(
            _mock_llm_serialized("child-model"),
            prompts=["x"],
            run_id="child-id",
            parent_run_id="root-id",
        )
        child_trace_id = tracer._pending["child-id"].trace_id

        assert child_trace_id == root_trace_id  # same trace
        assert child_trace_id is not None

        proc.shutdown()

    def test_each_root_gets_new_trace_id(self):
        """Two root spans without parent get different trace_ids."""
        proc, _spans = _make_capturer()
        tracer = AgentGrafTracer(processor=proc, project="test")

        tracer.on_chain_start(_mock_chain_serialized("run-a"), {}, run_id="a")
        tracer.on_chain_start(_mock_chain_serialized("run-b"), {}, run_id="b")

        assert tracer._pending["a"].trace_id != tracer._pending["b"].trace_id

        proc.shutdown()


# -- Chain hooks ---------------------------------------------------------

class TestChainHooks:
    def test_chain_start_and_end(self):
        proc, spans = _make_capturer()
        tracer = AgentGrafTracer(processor=proc, project="test")

        tracer.on_chain_start(
            _mock_chain_serialized("my-chain"),
            {"query": "what's up"},
            run_id="chain-1",
            tags=["production"],
        )

        tracer.on_chain_end({"answer": "not much"}, run_id="chain-1")

        proc.force_flush()
        proc.shutdown()

        assert len(spans) == 1
        s = spans[0]
        assert s.kind == SpanKind.CHAIN
        assert s.name == "my-chain"
        assert s.status == SpanStatus.OK
        assert s.end_time is not None


# -- Tool hooks ----------------------------------------------------------

class TestToolHooks:
    def test_tool_start_and_end(self):
        proc, spans = _make_capturer()
        tracer = AgentGrafTracer(processor=proc, project="test")

        tracer.on_tool_start(
            _mock_tool_serialized("kubectl_get_pods"),
            "get pods -n default",
            run_id="tool-1",
        )

        tracer.on_tool_end("pod1, pod2", run_id="tool-1")

        proc.force_flush()
        proc.shutdown()

        assert len(spans) == 1
        s = spans[0]
        assert s.kind == SpanKind.TOOL
        assert s.name == "kubectl_get_pods"
        assert s.input_data is not None
        assert "pod1" in (s.output_data or "")

    def test_tool_error(self):
        proc, spans = _make_capturer()
        tracer = AgentGrafTracer(processor=proc, project="test")

        tracer.on_tool_start(_mock_tool_serialized("broken"), "bad input", run_id="tool-err")
        tracer.on_tool_error(RuntimeError("connection refused"), run_id="tool-err")

        proc.force_flush()
        proc.shutdown()

        assert spans[0].status == SpanStatus.ERROR


# -- Agent hooks ---------------------------------------------------------

class TestAgentHooks:
    def test_agent_action_and_finish(self):
        proc, spans = _make_capturer()
        tracer = AgentGrafTracer(processor=proc, project="test")

        action = MagicMock()
        action.tool = "think"
        action.tool_input = "what should I do?"

        tracer.on_agent_action(action, run_id="agent-act")
        tracer.on_agent_finish(MagicMock(), run_id="agent-act")

        proc.force_flush()
        proc.shutdown()

        assert len(spans) == 1
        s = spans[0]
        assert s.kind == SpanKind.AGENT
        assert "think" in s.name


# -- Retriever hooks -----------------------------------------------------

class TestRetrieverHooks:
    def test_retriever_start_and_end(self):
        proc, spans = _make_capturer()
        tracer = AgentGrafTracer(processor=proc, project="test")

        tracer.on_retriever_start({"name": "my-retriever"}, "find docs about x", run_id="ret-1")
        tracer.on_retriever_end(["doc1", "doc2", "doc3"], run_id="ret-1")

        proc.force_flush()
        proc.shutdown()

        s = spans[0]
        assert s.kind == SpanKind.RETRIEVER
        assert "3 documents" in (s.output_data or "")


# -- Memory management ---------------------------------------------------

class TestMemoryManagement:
    def test_pop_pending_cleans_trace_ids(self):
        """After _pop_pending, the run_id should not remain in _trace_ids."""
        proc, _spans = _make_capturer()
        tracer = AgentGrafTracer(processor=proc, project="test")

        tracer.on_chain_start(_mock_chain_serialized("test-chain"), {}, run_id="mem-test")
        assert "mem-test" in tracer._trace_ids  # trace_id was stored

        tracer.on_chain_end({}, run_id="mem-test")

        assert "mem-test" not in tracer._trace_ids  # cleaned up
        assert "mem-test" not in tracer._pending
        proc.shutdown()

    def test_sweep_orphaned_spans(self):
        """Spans stuck in _pending past orphan_timeout get emitted as ERROR."""
        proc, spans = _make_capturer()
        tracer = AgentGrafTracer(
            processor=proc, project="test", orphan_timeout_seconds=0.01
        )

        # Manually insert a span with an old start_time
        old_span = (
            TraceSpan(
                run_id="stuck-span",
                trace_id=uuid.uuid4().hex,
                span_id="ab" * 8,
                project="test",
                kind=SpanKind.LLM,
                name="stuck-llm",
                start_time=time.time() - 10,  # 10 seconds ago
            )
        )
        with tracer._lock:
            tracer._pending["stuck-span"] = old_span
            tracer._trace_ids["stuck-span"] = old_span.trace_id

        # Trigger the 50th sweep by setting counter to 49 first
        tracer._sweep_counter = 49
        tracer.on_llm_start(_mock_llm_serialized(), prompts=["x"], run_id="trigger")
        tracer.on_llm_end(MagicMock(), run_id="trigger")

        proc.force_flush()
        proc.shutdown()

        # Should see: 1 trigger span (OK) + 1 orphaned span (ERROR)
        assert len(spans) == 2

        orphan = [s for s in spans if s.run_id == "stuck-span"]
        assert len(orphan) == 1
        assert orphan[0].status == SpanStatus.ERROR
        assert "orphaned" in (orphan[0].error or "")
