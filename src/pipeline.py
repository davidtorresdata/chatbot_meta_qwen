"""Worker-side processing of one queued message (runs inside the dispatcher).

orchestrator (tree / guardrails / RAG / Qwen) -> Meta send -> registry.
Every failure path ends with a reply to the customer (fallback) and a metric.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from src.agent.orchestrator import ReplyAction
from src.utils import metrics
from src.utils.pii import content_for_log, mask_phone
from src.utils.resilience import CircuitOpenError

if TYPE_CHECKING:
    from src.agent.orchestrator import WhatsAppOrchestrator
    from src.config import Settings
    from src.dispatch.base import InboundMessage
    from src.registry import ConversationRegistry
    from src.whatsapp.meta import MetaWhatsAppClient

logger = logging.getLogger(__name__)


class MessageProcessor:
    def __init__(
        self,
        settings: "Settings",
        orchestrator: "WhatsAppOrchestrator",
        whatsapp: "MetaWhatsAppClient",
        registry: "ConversationRegistry | None",
    ):
        self._settings = settings
        self._orchestrator = orchestrator
        self._whatsapp = whatsapp
        self._registry = registry

    async def __call__(self, message: "InboundMessage") -> None:
        started = time.perf_counter()
        metrics.QUEUE_WAIT_SECONDS.observe(max(0.0, time.time() - message.received_at))
        phone = message.phone
        outcome = "ok"
        try:
            action = await asyncio.wait_for(
                self._compute(message), timeout=self._settings.queue.processing_timeout_seconds
            )
        except asyncio.TimeoutError:
            outcome = "timeout"
            logger.error("Processing timeout for %s", mask_phone(phone))
            action = self._fallback()
        except CircuitOpenError:
            outcome = "circuit_open"
            action = self._fallback()
        except Exception:
            outcome = "error"
            logger.exception("Failed to process message from %s", mask_phone(phone))
            action = self._fallback()

        logger.info(
            "Action computed | phone=%s msg_id=%s type=%s outcome=%s reply=%s",
            mask_phone(phone), message.msg_id or "-", action.type, outcome, content_for_log(action.message),
        )
        try:
            if message.msg_id:
                await self._whatsapp.mark_read(message.msg_id)
            await self._send(phone, action)
        except Exception:
            outcome = "send_failed"
            logger.exception("Failed to send reply to %s", mask_phone(phone))

        metrics.MESSAGES_PROCESSED.labels(action.type, outcome).inc()
        metrics.PROCESSING_SECONDS.observe(time.perf_counter() - started)
        if self._registry is not None and message.kind == "text":
            await self._registry.log_turn(phone, message.text, action.message)

    async def _compute(self, message: "InboundMessage") -> ReplyAction:
        if message.kind != "text":
            return ReplyAction(type="text", message=self._settings.agent.unsupported_message)
        return await self._orchestrator.handle_message(message.phone, message.text)

    def _fallback(self) -> ReplyAction:
        return ReplyAction(type="fallback", message=self._settings.agent.fallback_message)

    async def _send(self, phone: str, action: ReplyAction) -> None:
        if action.type == "redirect" and action.url:
            await self._whatsapp.send_cta_url(phone, action.message, action.url)
        else:
            await self._whatsapp.send_text(phone, action.message)
