"""Ollama client used by every RuderAI component."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import httpx


@dataclass(frozen=True, slots=True)
class LLMGenerationConfig:
    temperature: float = 0.1
    num_ctx: int = 16384
    max_tokens: int = 3072


class OllamaClient:
    """Small, deterministic wrapper around Ollama's /api/chat endpoint."""

    DEFAULT_TIMEOUT = 300.0

    def __init__(
        self,
        model: str,
        host: str = "http://localhost:11434",
        timeout: float = DEFAULT_TIMEOUT,
        generation: LLMGenerationConfig | None = None,
        temperature: float | None = None,
        num_ctx: int | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = float(timeout)
        base = generation or LLMGenerationConfig()
        self.generation = LLMGenerationConfig(
            temperature=base.temperature if temperature is None else temperature,
            num_ctx=base.num_ctx if num_ctx is None else int(num_ctx),
            max_tokens=base.max_tokens if max_tokens is None else int(max_tokens),
        )

    def _payload(
        self,
        messages: Iterable[dict[str, str]],
        system_instruction: str | None = None,
    ) -> dict[str, Any]:
        payload_messages = list(messages)
        if system_instruction:
            payload_messages.insert(0, {"role": "system", "content": system_instruction})
        return {
            "model": self.model,
            "messages": payload_messages,
            "stream": False,
            "options": {
                "temperature": self.generation.temperature,
                "num_ctx": self.generation.num_ctx,
                "num_predict": self.generation.max_tokens,
            },
        }

    async def chat(self, messages: list[dict[str, str]], system_instruction: str | None = None) -> str:
        payload = self._payload(messages, system_instruction)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.host}/api/chat", json=payload)
                response.raise_for_status()
                data = response.json()
                return str(data.get("message", {}).get("content", "") or "")
        except httpx.ConnectError:
            return f"[LLM_ERROR] Ollama 서버에 연결할 수 없습니다. host={self.host}"
        except httpx.TimeoutException:
            return (
                f"[LLM_ERROR] Ollama 응답 시간 초과 ({self.timeout:.0f}초). "
                f"model={self.model} num_ctx={self.generation.num_ctx}"
            )
        except httpx.HTTPStatusError as exc:
            return f"[LLM_ERROR] Ollama HTTP 오류 {exc.response.status_code}: {exc.response.text[:500]}"
        except Exception as exc:
            return f"[LLM_ERROR] Ollama 호출 실패 ({type(exc).__name__}): {exc}"
