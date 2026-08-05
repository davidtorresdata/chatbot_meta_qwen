"""Embedding providers.

Two pluggable backends:
  * ``openai_compat`` - any OpenAI-compatible ``/v1/embeddings`` endpoint
    (vLLM, Ollama, LM Studio). Same client library as the LLM.
  * ``local``          - sentence-transformers model running on this machine.
    Fully offline, no API needed (install ``requirements-local.txt``).

The backend is selected through config ``embeddings.backend``.
"""

from __future__ import annotations

from typing import Protocol

from src.config import EmbeddingsConfig


class EmbeddingProvider(Protocol):
    """Produces dense vectors for a list of texts."""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    @property
    def dimension(self) -> int: ...


class OpenAICompatEmbedder:
    """Uses the same OpenAI-compatible endpoint as the LLM for embeddings."""

    def __init__(self, config: EmbeddingsConfig, llm_base_url: str | None = None):
        from openai import AsyncOpenAI

        self._base_url = config.base_url or llm_base_url or "http://localhost:8001/v1"
        self._api_key = config.api_key or "EMPTY"
        self._model = config.model
        self._client = AsyncOpenAI(base_url=self._base_url, api_key=self._api_key)
        self._dim: int | None = None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        response = await self._client.embeddings.create(model=self._model, input=texts)
        vectors = [item.embedding for item in response.data]
        if self._dim is None and vectors:
            self._dim = len(vectors[0])
        return vectors

    @property
    def dimension(self) -> int:
        if self._dim is None:
            raise RuntimeError("dimension unknown until first embed() call")
        return self._dim


class LocalEmbedder:
    """Offline sentence-transformers embeddings (run in a thread)."""

    def __init__(self, config: EmbeddingsConfig):
        if config.backend != "local":
            raise ValueError(f"LocalEmbedder requires backend='local', got {config.backend!r}")
        self._model_name = config.model
        self._model = None
        self._dim: int | None = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import asyncio

        def _run() -> list[list[float]]:
            self._load()
            vectors = self._model.encode(texts, normalize_embeddings=True).tolist()
            return vectors

        vectors = await asyncio.to_thread(_run)
        if self._dim is None and vectors:
            self._dim = len(vectors[0])
        return vectors

    @property
    def dimension(self) -> int:
        if self._dim is None:
            raise RuntimeError("dimension unknown until first embed() call")
        return self._dim


def build_embedder(config: EmbeddingsConfig, llm_base_url: str | None = None) -> EmbeddingProvider:
    if config.backend == "local":
        return LocalEmbedder(config)
    return OpenAICompatEmbedder(config, llm_base_url=llm_base_url)
