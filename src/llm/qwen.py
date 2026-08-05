"""Qwen LLM client over any OpenAI-compatible endpoint.

Works with vLLM, Ollama, LM Studio or the DashScope OpenAI-compatible API.
The endpoint, model and temperature come from settings (``llm`` section /
env overrides).
"""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI

from src.config import LLMConfig


class QwenClient:
    def __init__(self, config: LLMConfig):
        self._config = config
        self._client = AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key or "EMPTY",
            timeout=config.timeout_seconds,
        )

    async def chat(
        self,
        system: str,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
    ) -> str:
        """Single completion call. ``messages`` are prior turns (without system)."""
        request: list[dict[str, str]] = [{"role": "system", "content": system}, *messages]

        response = await self._client.chat.completions.create(
            model=self._config.model,
            messages=request,
            temperature=(
                temperature if temperature is not None else self._config.temperature
            ),
            max_tokens=self._config.max_tokens,
            top_p=self._config.top_p,
        )

        content: str | None = None
        if response.choices:
            content = response.choices[0].message.content
        return (content or "").strip()

    @property
    def temperature(self) -> float:
        return self._config.temperature

    async def close(self) -> None:
        try:
            await self._client.close()
        except Exception:
            pass
