"""Loki-direct HTTP client — dual sync/async, auto-detects execution context.

Push spans directly to Loki's push API. No Gateway required in v0.1.0.

Loki API reference: POST /loki/api/v1/push
Payload: {"streams": [{"stream": {...labels...}, "values": [[ts_ns, line, metadata]]}]}

Timestamps must be **string nanosecond-epoch** or Loki returns 400.
Structured metadata (3rd tuple element) is a Loki >=3.0 feature — older Loki
versions silently ignore it, so the fallback is the JSON body parsed via ``| json``
in LogQL.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from .models import TraceSpan

logger = logging.getLogger("agentgraf.client")

# Loki labels kept intentionally low-cardinality — never put run_id/trace_id here.
_STATIC_LABELS = {"job": "agentgraf"}


class LokiClient:
    """Push spans to Loki HTTP API. Dual sync/async, auto-detects context.

    Args:
        loki_url: Full Loki push endpoint (e.g. ``http://loki:3100/loki/api/v1/push``).
        max_retries: Number of retry attempts on transient failures (5xx, timeouts).
        retry_delay: Base delay in seconds before first retry (default 1.0).
        timeout: HTTP request timeout in seconds (default 10).
    """

    def __init__(
        self,
        loki_url: str,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        timeout: float = 10.0,
    ):
        self._url = loki_url.rstrip("/")
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._timeout = timeout
        self._sync_client: Optional[httpx.Client] = None
        self._async_client: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------
    #  Public API — sync
    # ------------------------------------------------------------------
    def send_spans_sync(self, spans: List[TraceSpan]) -> bool:
        """Push a batch of spans to Loki from a synchronous context.

        Retries on 5xx and network errors. Does NOT retry 4xx
        (client errors will not resolve on their own).
        """
        if self._sync_client is None:
            self._sync_client = httpx.Client(timeout=self._timeout)
        payload = _build_loki_payload(spans)
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = self._sync_client.post(
                    self._url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code < 300:
                    return True

                if 400 <= resp.status_code < 500:
                    # Client error — not retryable
                    logger.warning(
                        "Loki push returned %d (client error, not retrying): %s",
                        resp.status_code,
                        resp.text[:200],
                    )
                    return False

                # Server error (5xx) — retryable
                logger.warning(
                    "Loki push returned %d (attempt %d/%d): %s",
                    resp.status_code,
                    attempt,
                    self._max_retries,
                    resp.text[:200],
                )
            except httpx.HTTPError as exc:
                logger.warning(
                    "Loki push failed (attempt %d/%d): %s",
                    attempt,
                    self._max_retries,
                    exc,
                )
            if attempt < self._max_retries:
                time.sleep(self._retry_delay * (2 ** (attempt - 1)))
        logger.error(
            "Failed to push %d spans to Loki after %d attempts", len(spans), self._max_retries
        )
        return False

    # ------------------------------------------------------------------
    #  Public API — async
    # ------------------------------------------------------------------
    async def send_spans_async(self, spans: List[TraceSpan]) -> bool:
        """Push a batch of spans to Loki from an async context.

        Retries on 5xx and network errors. Does NOT retry 4xx.
        """
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(timeout=self._timeout)
        payload = _build_loki_payload(spans)
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = await self._async_client.post(
                    self._url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code < 300:
                    return True

                if 400 <= resp.status_code < 500:
                    logger.warning(
                        "Loki push returned %d (client error, not retrying): %s",
                        resp.status_code,
                        resp.text[:200],
                    )
                    return False

                logger.warning(
                    "Loki push returned %d (attempt %d/%d): %s",
                    resp.status_code,
                    attempt,
                    self._max_retries,
                    resp.text[:200],
                )
            except httpx.HTTPError as exc:
                logger.warning(
                    "Loki push failed (attempt %d/%d): %s",
                    attempt,
                    self._max_retries,
                    exc,
                )
            if attempt < self._max_retries:
                await asyncio.sleep(self._retry_delay * (2 ** (attempt - 1)))
        logger.error(
            "Failed to push %d spans to Loki after %d attempts", len(spans), self._max_retries
        )
        return False

    # ------------------------------------------------------------------
    #  Lifecycle
    # ------------------------------------------------------------------
    def close_sync(self) -> None:
        """Close the synchronous HTTP client."""
        if self._sync_client is not None:
            self._sync_client.close()
            self._sync_client = None

    async def close_async(self) -> None:
        """Close the asynchronous HTTP client."""
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None


# ======================================================================
#  Internal helpers
# ======================================================================


def _build_loki_payload(spans: List[TraceSpan]) -> Dict[str, Any]:
    """Build a Loki push-API payload from a batch of spans.

    Groups spans by (project, kind) to minimise stream count.
    Timestamps are string nanoseconds (Loki requirement).
    Structured metadata carries trace_id/span_id/run_id for Loki >=3.0;
    the same data is in the JSON body for users on older Loki with ``| json``.
    """
    groups: Dict[tuple, List[tuple]] = {}
    for span in spans:
        key = (span.project, span.kind.value)
        ts_ns = str(int(span.start_time * 1_000_000_000))
        line = span.to_json()
        meta = {
            "trace_id": span.trace_id,
            "span_id": span.span_id,
            "run_id": span.run_id,
        }
        if span.parent_span_id:
            meta["parent_span_id"] = span.parent_span_id
        groups.setdefault(key, []).append((ts_ns, line, meta))

    streams = []
    for (project, kind), values in groups.items():
        streams.append(
            {
                "stream": {**_STATIC_LABELS, "project": project, "kind": kind},
                "values": values,
            }
        )
    return {"streams": streams}
