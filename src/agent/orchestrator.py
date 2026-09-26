"""Orchestrator: full RAG pipeline for an incoming WhatsApp message.

Flow
----
  1. Guardrail: forbidden subject (never talks about how it was built).
  2. Guardrail: redirect intents (site / other WhatsApp vendor).
  3. Embed the question and retrieve the best knowledge chunks (LanceDB).
  4. Threshold gate: if no chunk clears the similarity threshold -> fallback.
  5. Qwen answers strictly from the retrieved context (system prompt).
  6. Post-check: answer must be grounded in the context, else fallback.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from src.agent.guardrails import Guardrails
from src.agent.prompts import build_system_prompt
from src.state.memory import MemoryStateStore
from src.utils.pii import mask_phone

if TYPE_CHECKING:
    from src.config import RedirectRule, Settings
    from src.knowledge.embedding import EmbeddingProvider
    from src.knowledge.vector_store import VectorStore
    from src.llm.qwen import QwenClient
    from src.state.base import StateStore

logger = logging.getLogger(__name__)


@dataclass
class ReplyAction:
    type: str  # "text" | "redirect" | "fallback"
    message: str
    url: str | None = None
    whatsapp: str | None = None


@dataclass
class Conversation:
    phone: str
    history: list[dict[str, str]] = field(default_factory=list)

    def add_turn(self, user: str, assistant: str) -> None:
        self.history.append({"role": "user", "content": user})
        self.history.append({"role": "assistant", "content": assistant})

    def trim(self, max_turns: int) -> None:
        while len(self.history) > max_turns * 2:
            self.history.pop(0)


class WhatsAppOrchestrator:
    def __init__(
        self,
        settings: "Settings",
        vector_store: "VectorStore",
        embedder: "EmbeddingProvider",
        qwen: "QwenClient",
        state_store: "StateStore | None" = None,
    ):
        self._settings = settings
        self._vector_store = vector_store
        self._embedder = embedder
        self._qwen = qwen
        self._guardrails = Guardrails(settings)
        # Working copies only live for the duration of one message; the source of
        # truth is the state store (bounded + TTL in memory, or shared in Redis).
        self._conversations: dict[str, Conversation] = {}
        self._state = state_store if state_store is not None else MemoryStateStore(
            settings.state.conversation_ttl_seconds, settings.state.max_conversations
        )
        self._tree = self._build_tree()

    # ------------------------------------------------------------------ API
    async def handle_message(self, phone: str, text: str) -> ReplyAction:
        """Process one message. Callers must not run two calls for the same
        phone concurrently (the dispatcher guarantees per-phone ordering)."""
        await self._load_state(phone)
        try:
            return await self._handle(phone, text)
        finally:
            await self._save_state(phone)

    async def _handle(self, phone: str, text: str) -> ReplyAction:
        text = text.strip()
        if not text:
            return self._fallback("")

        if self._tree is not None and self._tree.active(phone):
            tree_action = self._tree.handle(phone, text)
            if tree_action is not None:
                return tree_action

        refusal = self._guardrails.forbidden_subject(text)
        if refusal:
            return ReplyAction(type="text", message=refusal)

        redirect = self._guardrails.match_redirect(text)
        if redirect:
            return self._to_redirect(redirect)

        if self._tree is not None:
            tree_action = self._tree.handle(phone, text)
            if tree_action is not None:
                return tree_action

        hits = await self._retrieve(text)

        if not self._guardrails.retrieval_ok(hits):
            return self._fallback(text)

        context_texts = [hit.text for hit in hits[: self._settings.knowledge.top_k]]
        context = "\n---\n".join(
            f"[{i + 1}] {chunk}" for i, chunk in enumerate(context_texts)
        )

        conversation = self._conversations.setdefault(phone, Conversation(phone))
        conversation.trim(self._settings.agent.history_size)
        history = list(conversation.history) + [{"role": "user", "content": text}]

        system = build_system_prompt(self._settings, context)
        answer = await self._qwen.chat(
            system,
            history,
            temperature=self._qwen.temperature,
        )

        if not self._guardrails.is_grounded(answer, context_texts):
            logger.info("Answer rejected by grounding check for %s", mask_phone(phone))
            return self._fallback(text)

        conversation.add_turn(text, answer)
        return ReplyAction(type="text", message=answer)

    async def reset_conversation(self, phone: str) -> None:
        self._conversations.pop(phone, None)
        if self._tree is not None:
            self._tree.reset_conversation(phone)
        await self._state.delete(phone)

    # ---------------------------------------------------------------- state
    async def _load_state(self, phone: str) -> None:
        snapshot = await self._state.get(phone) or {}
        history = snapshot.get("history") or []
        if history:
            self._conversations[phone] = Conversation(phone, list(history))
        if self._tree is not None:
            self._tree.import_session(phone, snapshot.get("tree"))

    async def _save_state(self, phone: str) -> None:
        conversation = self._conversations.pop(phone, None)
        tree = None
        if self._tree is not None:
            tree = self._tree.export_session(phone)
            self._tree.forget(phone)
        if conversation:
            conversation.trim(self._settings.agent.history_size)
        history = conversation.history if conversation else []
        try:
            if history or tree:
                await self._state.set(phone, {"history": history, "tree": tree})
            else:
                await self._state.delete(phone)
        except Exception:
            logger.exception("Failed to persist conversation state for %s", mask_phone(phone))

    @property
    def fallback_message(self) -> str:
        return self._settings.agent.fallback_message

    # ------------------------------------------------------------------ util
    def _build_tree(self):
        if not self._settings.tree.enabled:
            return None
        from src.tree.engine import TreeEngine
        from src.tree.parser import load_tree

        path = Path(self._settings.tree.path)
        try:
            flows = {flow.id: flow for flow in load_tree(path)}
        except Exception:
            logger.exception("Failed to load conversation tree from %s", path)
            return None
        if not flows:
            logger.warning("Conversation tree is enabled but %s has no flows", path)
        return TreeEngine(flows, self._settings.tree)

    async def _retrieve(self, text: str) -> list:
        vectors = await self._embedder.embed([text])
        if not vectors:
            return []
        return await self._vector_store.search_async(
            vectors[0], top_k=self._settings.knowledge.top_k
        )

    def _fallback(self, _text: str) -> ReplyAction:
        message = self._settings.agent.fallback_message
        return ReplyAction(
            type="fallback",
            message=message,
            url=self._settings.redirects.default_url,
            whatsapp=self._settings.redirects.default_whatsapp,
        )

    def _to_redirect(self, rule: "RedirectRule") -> ReplyAction:
        action = ReplyAction(type="redirect", message=rule.message)
        if rule.whatsapp:
            action.whatsapp = rule.whatsapp
            digits = "".join(ch for ch in rule.whatsapp if ch.isdigit())
            action.url = f"https://wa.me/{digits}" if digits else None
        else:
            action.url = rule.url or self._settings.redirects.default_url
        return action
