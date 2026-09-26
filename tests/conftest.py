"""Shared pytest configuration.

``ruder_ai.agents.orchestrator`` only injects its deterministic benchmark
answers when ``RUDER_AI_BENCHMARK_FIXTURE_MODE=1``; otherwise it takes the
live-LLM path and the suite fails on any machine without a running Ollama.
Default the flag on for the test session so a bare ``pytest`` is reproducible,
while still honouring an explicit value from the environment.

This must stay above any ``ruder_ai`` import: the flag is read once at module
import time, so a conftest that imports the package first would be too late.
"""
import os

os.environ.setdefault("RUDER_AI_BENCHMARK_FIXTURE_MODE", "1")
