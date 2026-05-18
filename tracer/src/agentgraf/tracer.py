"""LangChain callback tracer — pushes spans to BatchSpanProcessor.

This module is loaded **only** when ``langchain-core`` is installed
(``pip install agentgraf[langchain]``). The core ``agentgraf`` package
is framework-agnostic and does not import this file.

Usage::

    from agentgraf.tracer import AgentGrafTracer
    from agentgraf.processor import BatchSpanProcessor
    from agentgraf.client import LokiClient

    client = LokiClient(loki_url="http://loki:3100/loki/api/v1/push")
    processor = BatchSpanProcessor(exporter=client.send_spans_sync)
    processor.start()

    tracer = AgentGrafTracer(processor=processor)

    # LangGraph:
    graph.astream(state, config={"callbacks": [tracer]})

    # LangChain:
    chain.invoke(input, config={"callbacks": [tracer]})

Design:
    We keep spans in memory between on_*_start and on_*_end.
    A span is emitted to the processor only once — at on_*_end,
    when we have the full picture (latency, tokens, error).

    ALL hooks are wrapped in try/except — tracing errors must NEVER
    crash the agent itself.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from langchain_core.callbacks.base import BaseCallbackHandler

from .models import SpanKind, SpanStatus, TraceSpan
from .processor import BatchSpanProcessor

logger = logging.getLogger("agentgraf.tracer")


class AgentGrafTracer(BaseCallbackHandler):
    """LangChain callback handler — one span per hook, emitted at _end.

    Uses LangChain's native ``run_id`` / ``parent_run_id`` (thread-safe).
    The first ``run_id`` of a chain becomes the OTel ``trace_id`` and is
    propagated to all descendant spans automatically.

    Args:
        processor: A started ``BatchSpanProcessor`` instance.
        project: Logical project name (default ``"default"``).
        io_truncate: Max chars for input_data/output_data (default 10 000).
        orphan_timeout_seconds: How long before a span without ``on_*_end``
            is considered orphaned and emitted as ERROR (default 300).
    """

    def __init__(
        self,
        processor: BatchSpanProcessor,
        project: str = "default",
        io_truncate: int = 10_000,
        orphan_timeout_seconds: int = 300,
    ):
        self._processor = processor
        self._project = project
        self._io_truncate = io_truncate
        self._orphan_timeout = orphan_timeout_seconds

        # Spans waiting for on_*_end — keyed by run_id (unique per hook invocation)
        self._pending: Dict[str, TraceSpan] = {}
        self._lock = threading.Lock()

        # Map LangChain run_id → OTel trace_id (first run_id in a chain = trace_id)
        self._trace_ids: Dict[str, str] = {}

        # Throttled orphan sweep — avoid O(n) scan on every call
        self._sweep_counter = 0

    # ==================================================================
    #  LLM hooks
    # ==================================================================
    def on_llm_start(self, serialized, prompts, *, run_id, parent_run_id=None,
                     tags=None, metadata=None, **kwargs):
        try:
            self._on_llm_start(serialized, prompts, run_id=run_id, parent_run_id=parent_run_id,
                               tags=tags, metadata=metadata, **kwargs)
        except Exception:
            logger.warning("Error in AgentGrafTracer.on_llm_start", exc_info=True)

    def _on_llm_start(self, serialized, prompts, run_id, parent_run_id=None,
                      tags=None, metadata=None, **kwargs):
        run_id = str(run_id)
        if parent_run_id:
            parent_run_id = str(parent_run_id)
        trace_id = self._get_trace_id(run_id, parent_run_id)
        model = _model_name(serialized)

        span = TraceSpan(
            trace_id=trace_id,
            span_id=_to16(run_id),
            parent_span_id=_to16(parent_run_id) if parent_run_id else None,
            run_id=run_id,
            run_name=self._run_name(metadata, tags),
            project=self._project,
            kind=SpanKind.LLM,
            name=model,
            model=model,
            start_time=time.time(),
            tags={t: True for t in tags} if tags else {},
            metadata=metadata or {},
        )
        if prompts:
            span.set_input(_serialize(prompts[0] if len(prompts) == 1 else prompts),
                          truncate=self._io_truncate)

        with self._lock:
            self._pending[run_id] = span

    def on_llm_end(self, response, *, run_id, parent_run_id=None, **kwargs):
        try:
            self._on_llm_end(response, run_id=run_id, parent_run_id=parent_run_id, **kwargs)
        except Exception:
            logger.warning("Error in AgentGrafTracer.on_llm_end", exc_info=True)

    def _on_llm_end(self, response, run_id, parent_run_id=None, **kwargs):
        run_id = str(run_id)
        if parent_run_id:
            parent_run_id = str(parent_run_id)
        span = self._pop_pending(run_id)
        if span is None:
            return

        span.finish()
        self._fill_tokens(response, span)
        # Extract output from the first generation (handle List[List] structure)
        if hasattr(response, 'generations') and response.generations:
            flat = response.generations[0]
            if isinstance(flat, list) and flat:
                flat = flat[0]
            msg = getattr(flat, 'message', None)
            if msg is not None:
                content = getattr(msg, 'content', '')
                if content:
                    span.set_output(str(content), truncate=self._io_truncate)

        self._processor.add_span(span)

    def on_llm_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        try:
            self._on_llm_error(error, run_id=run_id, parent_run_id=parent_run_id, **kwargs)
        except Exception:
            logger.warning("Error in AgentGrafTracer.on_llm_error", exc_info=True)

    def _on_llm_error(self, error, run_id, parent_run_id=None, **kwargs):
        run_id = str(run_id)
        if parent_run_id:
            parent_run_id = str(parent_run_id)
        span = self._pop_pending(run_id)
        if span is None:
            return

        span.finish(status=SpanStatus.ERROR, error=str(error)[:1000])
        self._processor.add_span(span)

    # ==================================================================
    #  Chain hooks
    # ==================================================================
    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None,
                       tags=None, metadata=None, **kwargs):
        try:
            self._on_chain_start(serialized, inputs, run_id=run_id, parent_run_id=parent_run_id,
                                 tags=tags, metadata=metadata, **kwargs)
        except Exception:
            logger.warning("Error in AgentGrafTracer.on_chain_start", exc_info=True)

    def _on_chain_start(self, serialized, inputs, run_id, parent_run_id=None,
                        tags=None, metadata=None, **kwargs):
        run_id = str(run_id)
        if parent_run_id:
            parent_run_id = str(parent_run_id)
        trace_id = self._get_trace_id(run_id, parent_run_id)
        langgraph_node = (metadata or {}).get("langgraph_node")
        serialized_name = _safe_chain_name(serialized)
        # Use the real class name from serialized (e.g. "RunnableSequence",
        # "ChatPromptTemplate") — only fall back to the LangGraph node name
        # when serialized yields nothing meaningful.
        # langgraph_node is still stored in span.metadata for filtering.
        name = serialized_name if serialized_name != "chain" else (langgraph_node or "chain")

        span = TraceSpan(
            trace_id=trace_id,
            span_id=_to16(run_id),
            parent_span_id=_to16(parent_run_id) if parent_run_id else None,
            run_id=run_id,
            run_name=self._run_name(metadata, tags),
            project=self._project,
            kind=SpanKind.CHAIN,
            name=name,
            start_time=time.time(),
            tags={t: True for t in tags} if tags else {},
            metadata=metadata or {},
        )
        if inputs:
            span.set_input(_serialize(inputs), truncate=self._io_truncate)

        with self._lock:
            self._pending[run_id] = span

    def on_chain_end(self, outputs, *, run_id, parent_run_id=None, **kwargs):
        try:
            self._on_chain_end(outputs, run_id=run_id, parent_run_id=parent_run_id, **kwargs)
        except Exception:
            logger.warning("Error in AgentGrafTracer.on_chain_end", exc_info=True)

    def _on_chain_end(self, outputs, run_id, parent_run_id=None, **kwargs):
        run_id = str(run_id)
        if parent_run_id:
            parent_run_id = str(parent_run_id)
        span = self._pop_pending(run_id)
        if span is None:
            return

        span.finish()
        if outputs:
            span.set_output(_serialize(outputs), truncate=self._io_truncate)
        self._processor.add_span(span)

    def on_chain_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        try:
            self._on_chain_error(error, run_id=run_id, parent_run_id=parent_run_id, **kwargs)
        except Exception:
            logger.warning("Error in AgentGrafTracer.on_chain_error", exc_info=True)

    def _on_chain_error(self, error, run_id, parent_run_id=None, **kwargs):
        run_id = str(run_id)
        if parent_run_id:
            parent_run_id = str(parent_run_id)
        span = self._pop_pending(run_id)
        if span is None:
            return

        span.finish(status=SpanStatus.ERROR, error=str(error)[:1000])
        self._processor.add_span(span)

    # ==================================================================
    #  Tool hooks
    # ==================================================================
    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None,
                      tags=None, metadata=None, **kwargs):
        try:
            self._on_tool_start(serialized, input_str, run_id=run_id, parent_run_id=parent_run_id,
                                tags=tags, metadata=metadata, **kwargs)
        except Exception:
            logger.warning("Error in AgentGrafTracer.on_tool_start", exc_info=True)

    def _on_tool_start(self, serialized, input_str, run_id, parent_run_id=None,
                       tags=None, metadata=None, **kwargs):
        run_id = str(run_id)
        if parent_run_id:
            parent_run_id = str(parent_run_id)
        trace_id = self._get_trace_id(run_id, parent_run_id)
        name = _safe_tool_name(serialized)

        span = TraceSpan(
            trace_id=trace_id,
            span_id=_to16(run_id),
            parent_span_id=_to16(parent_run_id) if parent_run_id else None,
            run_id=run_id,
            run_name=self._run_name(metadata, tags),
            project=self._project,
            kind=SpanKind.TOOL,
            name=name,
            start_time=time.time(),
            tags={t: True for t in tags} if tags else {},
            metadata=metadata or {},
        )
        if input_str:
            span.set_input(_serialize(input_str), truncate=self._io_truncate)

        with self._lock:
            self._pending[run_id] = span

    def on_tool_end(self, output, *, run_id, parent_run_id=None, **kwargs):
        try:
            self._on_tool_end(output, run_id=run_id, parent_run_id=parent_run_id, **kwargs)
        except Exception:
            logger.warning("Error in AgentGrafTracer.on_tool_end", exc_info=True)

    def _on_tool_end(self, output, run_id, parent_run_id=None, **kwargs):
        run_id = str(run_id)
        if parent_run_id:
            parent_run_id = str(parent_run_id)
        span = self._pop_pending(run_id)
        if span is None:
            return

        span.finish()
        if output:
            span.set_output(_serialize(output), truncate=self._io_truncate)
        self._processor.add_span(span)

    def on_tool_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        try:
            self._on_tool_error(error, run_id=run_id, parent_run_id=parent_run_id, **kwargs)
        except Exception:
            logger.warning("Error in AgentGrafTracer.on_tool_error", exc_info=True)

    def _on_tool_error(self, error, run_id, parent_run_id=None, **kwargs):
        run_id = str(run_id)
        if parent_run_id:
            parent_run_id = str(parent_run_id)
        span = self._pop_pending(run_id)
        if span is None:
            return

        span.finish(status=SpanStatus.ERROR, error=str(error)[:1000])
        self._processor.add_span(span)

    # ==================================================================
    #  Agent hooks
    # ==================================================================
    def on_agent_action(self, action, *, run_id, parent_run_id=None, **kwargs):
        try:
            self._on_agent_action(action, run_id=run_id, parent_run_id=parent_run_id, **kwargs)
        except Exception:
            logger.warning("Error in AgentGrafTracer.on_agent_action", exc_info=True)

    def _on_agent_action(self, action, run_id, parent_run_id=None, **kwargs):
        run_id = str(run_id)
        if parent_run_id:
            parent_run_id = str(parent_run_id)
        trace_id = self._get_trace_id(run_id, parent_run_id)
        tool = getattr(action, 'tool', 'unknown')
        tool_input = getattr(action, 'tool_input', str(action))

        span = TraceSpan(
            trace_id=trace_id,
            span_id=_to16(run_id),
            parent_span_id=_to16(parent_run_id) if parent_run_id else None,
            run_id=run_id,
            project=self._project,
            kind=SpanKind.AGENT,
            name=f"action:{tool}",
            start_time=time.time(),
        )
        span.set_input(_serialize(tool_input), truncate=self._io_truncate)

        with self._lock:
            self._pending[run_id] = span

    def on_agent_finish(self, finish, *, run_id, parent_run_id=None, **kwargs):
        try:
            self._on_agent_finish(finish, run_id=run_id, parent_run_id=parent_run_id, **kwargs)
        except Exception:
            logger.warning("Error in AgentGrafTracer.on_agent_finish", exc_info=True)

    def _on_agent_finish(self, finish, run_id, parent_run_id=None, **kwargs):
        run_id = str(run_id)
        if parent_run_id:
            parent_run_id = str(parent_run_id)
        span = self._pop_pending(run_id)
        if span is None:
            return

        span.finish()
        span.set_output(_serialize(finish), truncate=self._io_truncate)
        self._processor.add_span(span)

    # ==================================================================
    #  Retriever hooks
    # ==================================================================
    def on_retriever_start(self, serialized, query, *, run_id, parent_run_id=None, **kwargs):
        try:
            self._on_retriever_start(serialized, query, run_id=run_id, parent_run_id=parent_run_id, **kwargs)
        except Exception:
            logger.warning("Error in AgentGrafTracer.on_retriever_start", exc_info=True)

    def _on_retriever_start(self, serialized, query, run_id, parent_run_id=None, **kwargs):
        run_id = str(run_id)
        if parent_run_id:
            parent_run_id = str(parent_run_id)
        trace_id = self._get_trace_id(run_id, parent_run_id)
        name = _safe_retriever_name(serialized)

        span = TraceSpan(
            trace_id=trace_id,
            span_id=_to16(run_id),
            parent_span_id=_to16(parent_run_id) if parent_run_id else None,
            run_id=run_id,
            project=self._project,
            kind=SpanKind.RETRIEVER,
            name=name,
            start_time=time.time(),
        )
        span.set_input(_serialize(query), truncate=self._io_truncate)

        with self._lock:
            self._pending[run_id] = span

    def on_retriever_end(self, documents, *, run_id, parent_run_id=None, **kwargs):
        try:
            self._on_retriever_end(documents, run_id=run_id, parent_run_id=parent_run_id, **kwargs)
        except Exception:
            logger.warning("Error in AgentGrafTracer.on_retriever_end", exc_info=True)

    def _on_retriever_end(self, documents, run_id, parent_run_id=None, **kwargs):
        run_id = str(run_id)
        if parent_run_id:
            parent_run_id = str(parent_run_id)
        span = self._pop_pending(run_id)
        if span is None:
            return

        span.finish()
        n = len(documents) if isinstance(documents, list) else 0
        span.set_output(f"{n} documents", truncate=self._io_truncate)
        self._processor.add_span(span)

    def on_retriever_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        try:
            self._on_retriever_error(error, run_id=run_id, parent_run_id=parent_run_id, **kwargs)
        except Exception:
            logger.warning("Error in AgentGrafTracer.on_retriever_error", exc_info=True)

    def _on_retriever_error(self, error, run_id, parent_run_id=None, **kwargs):
        run_id = str(run_id)
        if parent_run_id:
            parent_run_id = str(parent_run_id)
        span = self._pop_pending(run_id)
        if span is None:
            return

        span.finish(status=SpanStatus.ERROR, error=str(error)[:1000])
        self._processor.add_span(span)

    # ==================================================================
    #  Internal helpers
    # ==================================================================
    def _get_trace_id(self, run_id: str, parent_run_id: Optional[str]) -> str:
        """Get or create the trace_id for this run."""
        # Inherit from parent
        if parent_run_id:
            with self._lock:
                tid = self._trace_ids.get(parent_run_id)
            if tid:
                self._set_trace(run_id, tid)
                return tid

        # Create new trace
        tid = uuid.uuid4().hex
        self._set_trace(run_id, tid)
        return tid

    def _set_trace(self, run_id: str, trace_id: str) -> None:
        with self._lock:
            self._trace_ids[run_id] = trace_id

    @staticmethod
    def _run_name(metadata: Optional[dict], tags: Optional[list]) -> Optional[str]:
        """Best-effort human-readable name: metadata.run_name > first tag."""
        if metadata and metadata.get("run_name"):
            return metadata["run_name"]
        if tags:
            return tags[0]
        return None

    def _pop_pending(self, run_id: str) -> Optional[TraceSpan]:
        """Remove and return a pending span. Sweeps orphans + cleans trace_ids.

        Orphan sweep is throttled — only runs every 50th call, unless the
        pending buffer grows large (>=100 entries), in which case we sweep
        immediately.
        """
        to_emit: List[TraceSpan] = []

        with self._lock:
            span = self._pending.pop(run_id, None)

            # Cleanup: remove trace_id entry so it doesn't grow forever
            self._trace_ids.pop(run_id, None)

            # ---- throttled orphan sweep ----
            self._sweep_counter += 1
            should_sweep = (
                self._sweep_counter >= 50
                or len(self._pending) >= 100
            )

            if should_sweep:
                self._sweep_counter = 0
                now = time.time()
                for rid, s in list(self._pending.items()):
                    if now - s.start_time > self._orphan_timeout:
                        s.finish(
                            status=SpanStatus.ERROR,
                            error=f"orphaned after {self._orphan_timeout}s",
                        )
                        self._pending.pop(rid, None)
                        self._trace_ids.pop(rid, None)
                        to_emit.append(s)
                        logger.warning("Swept orphaned span %s (%s)", s.name, rid)

        # Emit outside the lock to avoid nested locking
        for s in to_emit:
            self._processor.add_span(s)

        return span

    def _fill_tokens(self, response, span: TraceSpan) -> None:
        """Try to extract token counts from common response shapes.

        Note: ``response.generations`` is ``List[List[Generation]]`` in LangChain.
        We flatten it to inspect individual Generation objects.
        """
        if hasattr(response, 'generations'):
            for gen_list in (response.generations or []):
                for gen in (gen_list or []):
                    msg = getattr(gen, 'message', None)
                    if msg and hasattr(msg, 'usage_metadata'):
                        um = msg.usage_metadata or {}
                        if um.get('input_tokens') or um.get('output_tokens'):
                            span.set_tokens(
                                input_tokens=um.get('input_tokens', 0),
                                output_tokens=um.get('output_tokens', 0),
                            )
                            return

        # Older LangChain llm_output.token_usage
        llm_output = getattr(response, 'llm_output', None) or {}
        tu = llm_output.get('token_usage', {})
        if tu.get('prompt_tokens') or tu.get('completion_tokens'):
            span.set_tokens(
                input_tokens=tu.get('prompt_tokens', 0),
                output_tokens=tu.get('completion_tokens', 0),
            )


# ======================================================================
#  Standalone helpers
# ======================================================================


def _to16(run_id: str) -> str:
    """Convert LangChain UUID -> 16-char OTel span_id."""
    if isinstance(run_id, uuid.UUID):
        return run_id.hex[:16]
    return str(run_id).replace("-", "")[:16]


def _model_name(serialized: dict) -> str:
    """Extract model name from serialized LLM info (best-effort)."""
    if not isinstance(serialized, dict):
        return "unknown-llm"
    kw = serialized.get("kwargs", {}) or {}
    return str(
        kw.get("model_name")
        or kw.get("model")
        or kw.get("deployment_name")
        or serialized.get("id", ["unknown"])[-1]
    )


def _safe_chain_name(serialized) -> str:
    """Safely extract chain name from serialized dict."""
    if not isinstance(serialized, dict):
        return "chain"
    name = serialized.get("name")
    if name:
        return str(name)
    ids = serialized.get("id", ["chain"])
    if isinstance(ids, list) and ids:
        return str(ids[-1])
    return "chain"


def _safe_tool_name(serialized) -> str:
    """Safely extract tool name from serialized dict."""
    if not isinstance(serialized, dict):
        return "unknown-tool"
    return str(serialized.get("name", "unknown-tool"))


def _safe_retriever_name(serialized) -> str:
    """Safely extract retriever name from serialized dict."""
    if not isinstance(serialized, dict):
        return "retriever"
    return str(serialized.get("name", "retriever"))


def _serialize(obj, truncate: int = 10_000) -> str:
    """Safe JSON serialisation with truncation."""
    import json

    try:
        s = json.dumps(obj, default=str, ensure_ascii=False)
    except Exception:
        s = str(obj)
    return s[:truncate] if len(s) > truncate else s
