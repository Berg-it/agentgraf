"""AgentGraf — Zero-infrastructure AI agent tracing for Grafana + Loki.

Usage::

    from agentgraf import LokiClient, BatchSpanProcessor, TraceSpan

    client = LokiClient(loki_url="http://loki:3100/loki/api/v1/push")
    processor = BatchSpanProcessor(exporter=client.send_spans_sync)
    processor.start()

    # ... add spans manually or use AgentGrafTracer (LangChain) ...

    processor.shutdown()

Optional LangChain integration (``pip install agentgraf[langchain]``)::

    from agentgraf import AgentGrafTracer
    tracer = AgentGrafTracer(processor=processor)
    graph.astream(state, config={"callbacks": [tracer]})
"""

from .models import SpanKind, SpanStatus, TraceSpan
from .client import LokiClient
from .processor import BatchSpanProcessor

__all__ = [
    "TraceSpan",
    "SpanKind",
    "SpanStatus",
    "LokiClient",
    "BatchSpanProcessor",
]

# Lazy import for LangChain tracer — only available if langchain-core is installed.
try:
    from .tracer import AgentGrafTracer  # noqa: F401

    __all__.append("AgentGrafTracer")
except ImportError:
    pass
