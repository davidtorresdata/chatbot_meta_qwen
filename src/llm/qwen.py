"""Qwen LLM client over any OpenAI-compatible endpoint.

Works with vLLM, Ollama, LM Studio or the DashScope OpenAI-compatible API.
The endpoint, model and temperature come from settings (``llm`` section /
env overrides).
"""

from __future__ import annotations

import time

from openai import AsyncOpenAI

from src.config import LLMConfig
from src.utils import metrics
from src.utils.resilience import CircuitBreaker


class QwenClient:
    def __init__(self, config: LLMConfig):
        self._config = config
        self._client = AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key or "EMPTY",
            timeout=config.timeout_seconds,
            max_retries=config.max_retries,  # SDK: exponential backoff on 408/429/5xx/network
        )
        self.breaker = CircuitBreaker(
            "llm", config.circuit_failure_threshold, config.circuit_cooldown_seconds
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

        self.breaker.before_call()  # raises CircuitOpenError -> caller falls back fast
        started = time.perf_counter()
        try:
            response = await self._client.chat.completions.create(
                model=self._config.model,
                messages=request,
                temperature=(
                    temperature if temperature is not None else self._config.temperature
                ),
                max_tokens=self._config.max_tokens,
                top_p=self._config.top_p,
            )
        except Exception as exc:
            self.breaker.record_failure()
            metrics.LLM_ERRORS.labels(type(exc).__name__).inc()
            metrics.CIRCUIT_OPEN.labels("llm").set(1 if self.breaker.is_open else 0)
            raise
        self.breaker.record_success()
        metrics.CIRCUIT_OPEN.labels("llm").set(0)
        metrics.LLM_SECONDS.observe(time.perf_counter() - started)

        content: str | None = None
        if response.choices:
            content = response.choices[0].message.content
        return (content or "").strip()

    @property
    def temperature(self) -> float:
        return self._config.temperature

    async def ping(self, timeout: float = 3.0) -> bool:
        """Readiness probe: the OpenAI-compatible endpoint answers /models."""
        try:
            await self._client.with_options(timeout=timeout, max_retries=0).models.list()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        try:
            await self._client.close()
        except Exception:
            pass
