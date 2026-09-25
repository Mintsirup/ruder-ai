from __future__ import annotations

import asyncio

from ruder_ai.core.llm import LLMGenerationConfig, OllamaClient
from ruder_ai.core.settings import RuderAISettings


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("RUDER_AI_MODEL", "test-model")
    monkeypatch.setenv("RUDER_AI_NUM_CTX", "16384")
    monkeypatch.setenv("RUDER_AI_MAX_TOKENS", "4096")
    monkeypatch.setenv("RUDER_AI_CONTEXT_TOKEN_BUDGET", "9000")
    settings = RuderAISettings.from_env()
    assert settings.model == "test-model"
    assert settings.num_ctx == 16384
    assert settings.max_tokens == 4096
    assert settings.context_token_budget == 9000


def test_llm_payload_uses_ollama_context_options():
    client = OllamaClient(
        model="qwen2.5-coder:7b",
        generation=LLMGenerationConfig(
            temperature=0.1,
            num_ctx=8192,
            max_tokens=3072,
        ),
    )
    payload = client._payload([{"role": "user", "content": "hello"}])
    assert payload["options"] == {
        "temperature": 0.1,
        "num_ctx": 8192,
        "num_predict": 3072,
    }


def test_settings_defaults_are_real_values():
    settings = RuderAISettings.from_env()
    assert isinstance(settings.max_files, int)
    assert isinstance(settings.max_snippets_per_file, int)
    assert settings.max_files == 5
    assert settings.max_snippets_per_file == 5
    assert settings.num_ctx == 16384


def test_agent_can_construct_with_env_settings(tmp_path, monkeypatch):
    from ruder_ai.core.agent import AIAgent

    monkeypatch.setenv("RUDER_AI_MODEL", "test-model")
    monkeypatch.setenv("RUDER_AI_NUM_CTX", "8192")
    settings = RuderAISettings.from_env()
    agent = AIAgent(workspace_path=str(tmp_path), settings=settings)
    assert agent.settings is settings
    assert agent.context_builder.max_files == 5
    assert agent.context_builder.max_snippets_per_file == 5
    assert agent.llm.generation.num_ctx == 8192
