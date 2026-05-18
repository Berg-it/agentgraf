"""AgentGraf data models — Pydantic v2 span representation for LLM agent traces.

All timestamps are float unix-epoch seconds (compatible with Loki nanosecond push).
The model mirrors OpenTelemetry conventions where possible so that future
exporters (OTLP, Jaeger, Zipkin) are trivial to add.

contract-version: 1
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SpanKind(str, Enum):
    """OpenTelemetry SpanKind adapted for LLM workloads."""

    LLM = "llm"
    TOOL = "tool"
    CHAIN = "chain"
    AGENT = "agent"
    RETRIEVER = "retriever"


class SpanStatus(str, Enum):
    OK = "ok"
    ERROR = "error"


class TraceSpan(BaseModel):
    """A single span in an AI-agent trace.

    This is the stable data contract — backward compatible for life.
    Fields mirror OpenTelemetry conventions where possible.

    Attributes:
        trace_id: 32-hex-char UUID, shared by all spans in one logical run.
        span_id: 16-hex-char UUID, unique per span.
        parent_span_id: 16-hex-char UUID or ``None`` for root spans.
        run_id: Stable ID across a full agent invocation (LangChain ``run_id``).
        run_name: Optional human-readable label for the run.
        project: Logical grouping (e.g. ``k-fix``, ``support-bot``).
        kind: Semantic classification of the span.
        name: Human-readable operation name (``gpt-4o``, ``kubectl_get_pods``).
        start_time: Unix-epoch seconds.
        end_time: Unix-epoch seconds (``None`` until span is closed).
        latency_ms: Computed from (end_time - start_time) * 1000.
        model: LLM model name (OpenAI, Anthropic, etc.).
        input_tokens: Token count consumed by the prompt.
        output_tokens: Token count produced by the completion.
        total_tokens: ``input_tokens + output_tokens``.
        input_data: Truncated/sanitized input payload (JSON string).
        output_data: Truncated/sanitized output payload (JSON string).
        status: ``"ok"`` or ``"error"``.
        error: Error message when status is ``"error"``.
        tags: Free-form key/value labels.
        metadata: Structured metadata (extensible).
        agentgraf_version: Protocol version (current = 1).
    """

    # ── OTel-compatible IDs ──
    trace_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        description="32-char hex (OTel format)",
    )
    span_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex[:16],
        description="16-char hex",
    )
    parent_span_id: Optional[str] = Field(
        default=None,
        description="16-char hex or None (root span)",
    )

    # ── Identification ──
    run_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex[:16],
        description="Stable across a full agent invocation",
    )
    run_name: Optional[str] = None
    project: str = "default"

    # ── Span metadata ──
    kind: SpanKind = SpanKind.CHAIN
    name: str = "unnamed"
    start_time: float = Field(default_factory=time.time)
    end_time: Optional[float] = None
    latency_ms: Optional[int] = None

    # ── LLM-specific ──
    model: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    # ── I/O (sanitized, truncated) ──
    input_data: Optional[str] = None
    output_data: Optional[str] = None

    # ── Status ──
    status: SpanStatus = SpanStatus.OK
    error: Optional[str] = None

    # ── Extensibility ──
    tags: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # ── AgentGraf protocol version ──
    agentgraf_version: int = 1

    # ------------------------------------------------------------------
    #  Life-cycle helpers
    # ------------------------------------------------------------------
    def finish(
        self,
        end_time: Optional[float] = None,
        status: Optional[SpanStatus] = None,
        error: Optional[str] = None,
    ) -> "TraceSpan":
        """Close the span, recording end_time, latency, and optional status."""
        self.end_time = end_time or time.time()
        self.latency_ms = int((self.end_time - self.start_time) * 1000)
        if status is not None:
            self.status = status
        if error is not None:
            self.error = error
        return self

    def set_tag(self, key: str, value: Any) -> "TraceSpan":
        """Fluent helper to add a single tag."""
        self.tags[key] = value
        return self

    def set_metadata(self, key: str, value: Any) -> "TraceSpan":
        """Fluent helper to add a single metadata entry."""
        self.metadata[key] = value
        return self

    def set_input(self, data: str, truncate: int = 10_000) -> "TraceSpan":
        """Set input_data with optional truncation."""
        self.input_data = data[:truncate] if len(data) > truncate else data
        return self

    def set_output(self, data: str, truncate: int = 10_000) -> "TraceSpan":
        """Set output_data with optional truncation."""
        self.output_data = data[:truncate] if len(data) > truncate else data
        return self

    def set_tokens(self, input_tokens: int, output_tokens: int) -> "TraceSpan":
        """Record token usage."""
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = input_tokens + output_tokens
        return self

    # ------------------------------------------------------------------
    #  Serialization
    # ------------------------------------------------------------------
    def to_json(self) -> str:
        """Compact JSON string (one line → friendly for Loki / stdout)."""
        return self.model_dump_json(exclude_none=True)
