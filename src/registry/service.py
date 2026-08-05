"""High-level registry service: builds records, enriches and persists them."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from src.registry.backends import RegistryBackend, create_backend
from src.registry.contacts import ContactDirectory
from src.registry.models import ConversationRecord

if TYPE_CHECKING:
    from src.config import ConversationLogConfig, Settings

logger = logging.getLogger(__name__)


class ConversationRegistry:
    """Logs conversation turns, enriched with the contact directory.

    Writes are pushed to a worker thread so the synchronous SQLite / Google
    Sheets calls never block the FastAPI event loop. Persistence failures are
    logged but never crash the message flow.
    """

    def __init__(self, config: "ConversationLogConfig", backend: RegistryBackend):
        self._config = config
        self._backend = backend
        self._contacts = (
            ContactDirectory(config.contacts_file) if config.contacts_file else ContactDirectory()
        )

    async def log_turn(self, numero: str, pregunta: str, respuesta: str) -> None:
        try:
            record = self._build_record(numero, pregunta, respuesta)
            await asyncio.to_thread(self._backend.log, record)
        except Exception:
            logger.exception("Failed to persist conversation record for %s", numero)

    def _build_record(
        self, numero: str, pregunta: str, respuesta: str
    ) -> ConversationRecord:
        contact = self._contacts.lookup(numero)
        return ConversationRecord(
            hora=self._now(),
            numero=numero,
            nombre=contact.get("nombre", ""),
            ciudad=contact.get("ciudad", ""),
            empresa=contact.get("empresa", ""),
            pregunta=pregunta,
            respuesta=respuesta,
        )

    def _now(self) -> str:
        tz_name = (self._config.timezone or "UTC").strip()
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = timezone.utc
        return datetime.now(tz).isoformat(timespec="seconds")

    def close(self) -> None:
        try:
            self._backend.close()
        except Exception:
            logger.exception("Failed to close conversation registry backend")


def build_registry(settings: "Settings") -> ConversationRegistry | None:
    """Return a configured registry, or ``None`` when the feature is disabled."""
    config = settings.conversation_log
    if not config.enabled:
        logger.info("Conversation registry is disabled (CONVERSATION_LOG_ENABLED=0)")
        return None
    backend = create_backend(config)
    return ConversationRegistry(config, backend)
