# Changelog

All notable changes to AgentGraf will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] — 2026-05-18

### Changed
- First stable release — public API is now considered stable and follows Semantic Versioning
- Bumped version from `0.2.1` to `1.0.0`

### Fixed
- Classifier updated to `Production/Stable` in package metadata

## [0.1.0] — 2026-05-12

### Added
- `TraceSpan`, `SpanKind`, `SpanStatus` — Pydantic v2 data models aligned with OpenTelemetry
- `LokiClient` — dual sync/async HTTP client pushing spans directly to Loki push API
- `BatchSpanProcessor` — background-thread span processor with ring buffer, retry, and graceful shutdown
- `AgentGrafTracer` — LangChain callback handler with hooks for LLM, Chain, Tool, Agent, and Retriever
- Context manager (`with` statement) support on `BatchSpanProcessor`
- `CONTRIBUTING.md` with code style and PR guidelines
- 54 unit tests covering models, client, processor, and tracer
- Grafana panel plugin: `RunsPanel` (runs list with KPIs + duration bars) and `TraceTreePanel` (hierarchical span tree)
- Loki data frame parser (`parseLokiFrames`) and span tree builder (`buildSpanTree`)
- `py.typed` marker (PEP 561 compliance)

### Key design decisions
- **No Gateway in v0.1.0** — tracer pushes straight to Loki
- **langchain-core is optional** — `pip install agentgraf[langchain]` for the callback
- **Low-cardinality Loki labels** — only `job`, `project`, `kind`
- **Single span emission** — spans kept in memory between `on_*_start` / `on_*_end`, emitted once
- **No retry on 4xx** — client errors are not retryable
