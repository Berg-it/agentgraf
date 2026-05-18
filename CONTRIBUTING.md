# Contributing to AgentGraf

Thanks for your interest in contributing! AgentGraf is a simple, open-to-modification tracing library for AI agents. We keep things minimal — no over-engineering.

## Getting Started

```bash
git clone https://github.com/Berg-it/agentgraf.git
cd agentgraf
python3 -m venv .venv
.venv/bin/pip install -e "tracer/[dev,langchain]"
```

Run tests:
```bash
PYTHONPATH=tracer/src .venv/bin/pytest tracer/tests/ -v
```

## Project Structure

```
tracer/          → Python package (PyPI)
grafana-plugin/  → Grafana panel plugin (React/TypeScript)
gateway/         → FastAPI server (v0.2.0+)
examples/        → Usage examples
```

## Code Style

- **Simple code** — if a junior dev can't understand it, it's too clever.
- Imports at the top of the file (PEP 8), no inline imports in functions.
- Ruff + mypy configured in `pyproject.toml`.
- Docstrings: Google style.

Before submitting a PR:
```bash
ruff check tracer/
mypy tracer/src/
PYTHONPATH=tracer/src pytest tracer/tests/ -v
```

## Pull Requests

1. Fork and create a branch.
2. Keep PRs focused — one feature or fix per PR.
3. Add tests for new code.
4. Keep commits clean and descriptive.

## Issues

- Bug reports: include steps to reproduce and Python version.
- Feature requests: explain the use case, not just the solution.

## License

MIT — see [LICENSE](LICENSE).
