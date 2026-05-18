"""Background-thread batch span processor — non-blocking for the agent.

Collects spans in a ring buffer, flushes periodically or when the batch
is full. Inspired by OpenTelemetry's ``BatchSpanProcessor``.

Usage (context manager — recommended)::

    from agentgraf.client import LokiClient
    from agentgraf.processor import BatchSpanProcessor

    client = LokiClient(loki_url="http://loki:3100/loki/api/v1/push")

    with BatchSpanProcessor(exporter=client.send_spans_sync) as processor:
        # ... agent runs, spans are added via processor.add_span() ...
        # shutdown is automatic, even on exceptions

Usage (manual start/shutdown)::

    processor = BatchSpanProcessor(exporter=client.send_spans_sync)
    processor.start()
    # ... agent runs ...
    processor.shutdown()
"""
from __future__ import annotations

import atexit
import logging
import threading
import time
from typing import Callable, List, Optional

from .models import TraceSpan

logger = logging.getLogger("agentgraf.processor")


class BatchSpanProcessor:
    """Background-thread span processor. Never blocks the agent thread.

    Args:
        exporter: Callable ``(List[TraceSpan]) -> bool`` — typically
            ``LokiClient.send_spans_sync`` because the exporter is *always*
            called from the background thread (sync context).
        max_queue_size: Ring buffer capacity (default 1 000).
        max_batch_size: How many spans to send per flush (default 50).
        schedule_delay: Seconds between periodic flushes (default 5.0).
    """

    def __init__(
        self,
        exporter: Callable[[List[TraceSpan]], bool],
        max_queue_size: int = 1000,
        max_batch_size: int = 50,
        schedule_delay: float = 5.0,
    ):
        if max_batch_size > max_queue_size:
            raise ValueError("max_batch_size must be <= max_queue_size")

        self._exporter = exporter
        self._max_queue_size = max_queue_size
        self._max_batch_size = max_batch_size
        self._schedule_delay = schedule_delay

        self._buffer: List[TraceSpan] = []
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._dropped_count = 0

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Launch the background flush loop (daemon thread)."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="agentgraf-flush")
        self._thread.start()
        atexit.register(self.shutdown)

    # -- context manager -------------------------------------------------
    def __enter__(self) -> "BatchSpanProcessor":
        """Start the processor. For use with ``with`` statement."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Shutdown the processor — called at end of ``with`` block."""
        self.shutdown()

    def add_span(self, span: TraceSpan) -> None:
        """Enqueue a single span. Drops if the buffer is full. Non-blocking."""
        with self._lock:
            if len(self._buffer) >= self._max_queue_size:
                self._dropped_count += 1
                if self._dropped_count % 100 == 0:
                    logger.warning(
                        "Dropped %d spans so far — buffer full (capacity=%d)",
                        self._dropped_count,
                        self._max_queue_size,
                    )
                return
            self._buffer.append(span)

    def force_flush(self) -> None:
        """Synchronous flush — blocks until the current batch is exported."""
        self._flush()

    def shutdown(self, timeout: float = 5.0) -> None:
        """Flush remaining spans and stop the background thread.

        Called automatically via ``atexit`` — you can also call it manually.
        """
        self._running = False
        self._flush()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("BatchSpanProcessor background thread did not stop within %ss", timeout)

    # ------------------------------------------------------------------
    #  Internal
    # ------------------------------------------------------------------
    def _flush(self) -> None:
        """Take up to ``max_batch_size`` spans from the buffer and export them.

        On failure, failed spans are re-queued at the head of the buffer
        (preserving insertion order as much as possible).
        """
        with self._lock:
            if not self._buffer:
                return
            batch_size = min(self._max_batch_size, len(self._buffer))
            batch = self._buffer[:batch_size]
            self._buffer = self._buffer[batch_size:]

        # Export outside the lock — may take seconds (network I/O).
        success = self._exporter(batch)

        if not success:
            with self._lock:
                # Re-queue at the head, capped by available space.
                available = self._max_queue_size - len(self._buffer)
                re_queue = batch[:available]
                self._buffer = re_queue + self._buffer
                dropped = len(batch) - len(re_queue)
                if dropped:
                    self._dropped_count += dropped
                    logger.warning(
                        "Re-queue overflow: %d spans dropped (buffer full)", dropped
                    )

    def _run_loop(self) -> None:
        while self._running:
            time.sleep(self._schedule_delay)
            self._flush()
