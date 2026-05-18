"""Tests for agentgraf.processor — BatchSpanProcessor."""
import threading
import time

import pytest

from agentgraf.models import SpanKind, TraceSpan
from agentgraf.processor import BatchSpanProcessor


def _make_span(name="test"):
    """Quick span factory."""
    span = TraceSpan(run_id="run-1", name=name)
    span.finish(end_time=span.start_time + 0.1)
    return span


class TestBatchSpanProcessor:
    """Core behaviour: enqueue, flush, overflow, shutdown."""

    def test_add_span_enqueues(self):
        """add_span should put a span in the buffer."""
        exported = []
        processor = BatchSpanProcessor(
            exporter=lambda batch: exported.extend(batch) or True,
            max_queue_size=10,
            max_batch_size=5,
        )
        processor.start()

        span = _make_span("s1")
        processor.add_span(span)

        # Force flush to see it in the exported list
        processor.force_flush()
        processor.shutdown()

        assert len(exported) == 1
        assert exported[0].name == "s1"

    def test_flush_emits_batch(self):
        """Multiple spans should be flushed together."""
        exported = []
        processor = BatchSpanProcessor(
            exporter=lambda batch: exported.extend(batch) or True,
            max_queue_size=20,
            max_batch_size=10,
        )
        processor.start()

        for i in range(5):
            processor.add_span(_make_span(f"span-{i}"))

        processor.force_flush()
        processor.shutdown()

        assert len(exported) == 5
        names = [s.name for s in exported]
        assert names == ["span-0", "span-1", "span-2", "span-3", "span-4"]

    def test_large_batch_splits_across_flushes(self):
        """If more spans than max_batch_size, flush only takes N at a time."""
        exported = []
        flush_count = [0]

        def counting_exporter(batch):
            flush_count[0] += 1
            exported.extend(batch)
            return True

        processor = BatchSpanProcessor(
            exporter=counting_exporter,
            max_queue_size=20,
            max_batch_size=3,
        )
        processor.start()

        for i in range(7):
            processor.add_span(_make_span(f"span-{i}"))

        # Force flush repeatedly until buffer is drained
        for _ in range(5):
            processor.force_flush()

        processor.shutdown()

        assert len(exported) == 7
        assert flush_count[0] >= 3  # 7 spans / 3 per batch = 3 flushes

    def test_buffer_overflow_drops_spans(self):
        """When buffer is full, new spans are dropped."""
        exported = []
        processor = BatchSpanProcessor(
            exporter=lambda batch: exported.extend(batch) or True,
            max_queue_size=3,
            max_batch_size=3,
        )
        processor.start()

        # Fill the buffer
        for i in range(3):
            processor.add_span(_make_span(f"keep-{i}"))

        # These should be dropped
        for i in range(5):
            processor.add_span(_make_span(f"drop-{i}"))

        processor.force_flush()
        processor.shutdown()

        assert len(exported) == 3
        assert processor._dropped_count == 5

    def test_failed_export_requeues(self):
        """If the exporter returns False, spans go back to the buffer."""
        call_count = [0]

        def fail_then_succeed(batch):
            call_count[0] += 1
            if call_count[0] == 1:
                return False  # first attempt fails
            return True

        processor = BatchSpanProcessor(
            exporter=fail_then_succeed,
            max_queue_size=10,
            max_batch_size=5,
        )
        processor.start()

        processor.add_span(_make_span("persist-me"))
        processor.force_flush()  # fails → re-queued
        processor.force_flush()  # succeeds

        processor.shutdown()

        assert call_count[0] == 2

    def test_shutdown_flushes_remaining(self):
        """shutdown() should flush whatever is left in the buffer."""
        exported = []
        processor = BatchSpanProcessor(
            exporter=lambda batch: exported.extend(batch) or True,
            max_queue_size=10,
            max_batch_size=10,
            schedule_delay=0.01,  # fast for tests
        )
        processor.start()

        processor.add_span(_make_span("last-one"))
        processor.shutdown()

        assert len(exported) == 1

    def test_shutdown_idempotent(self):
        """Calling shutdown twice should not crash."""
        processor = BatchSpanProcessor(
            exporter=lambda batch: True,
        )
        processor.start()
        processor.shutdown()
        processor.shutdown()  # second call — no crash

    def test_background_thread_is_daemon(self):
        """The flush thread should be a daemon thread."""
        processor = BatchSpanProcessor(exporter=lambda batch: True)
        processor.start()

        assert processor._thread is not None
        assert processor._thread.daemon is True

        processor.shutdown()

    def test_context_manager_auto_shutdown(self):
        """With-statement should start and shutdown automatically."""
        exported = []
        processor = BatchSpanProcessor(
            exporter=lambda batch: exported.extend(batch) or True,
            schedule_delay=0.01,
        )

        assert not processor._running  # not started yet

        with processor as p:
            assert processor._running  # started
            assert p is processor
            p.add_span(_make_span("ctx-span"))
            p.force_flush()

        assert not processor._running  # shut down
        assert len(exported) == 1
        assert exported[0].name == "ctx-span"

    def test_context_manager_on_exception(self):
        """Shutdown still called even if an exception occurs inside with block."""
        exported = []
        processor = BatchSpanProcessor(
            exporter=lambda batch: exported.extend(batch) or True,
            schedule_delay=0.01,
        )

        try:
            with processor as p:
                p.add_span(_make_span("will-flush"))
                p.force_flush()
                raise RuntimeError("simulated crash")
        except RuntimeError:
            pass  # expected

        assert not processor._running  # shut down despite exception
        assert len(exported) == 1
        assert exported[0].name == "will-flush"
