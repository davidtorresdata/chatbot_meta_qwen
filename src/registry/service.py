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
from src.utils import metrics
from src.utils.pii import mask_phone

if TYPE_CHECKING:
    from src.config import ConversationLogConfig, Settings

logger = logging.getLogger(__name__)


class ConversationRegistry:
    """Logs conversation turns, enriched with the contact directory.

    Two modes:

    * **Background writer** (after :meth:`start`, used by the web app): turns go
      to a bounded in-memory buffer and ONE writer task persists them in batches
      (``batch_size`` rows or every ``flush_interval`` seconds). A single writer
      means no concurrent access to SQLite / the gspread client, and batching
      keeps Google Sheets far below its write quota.
    * **Direct** (no ``start``; CLI scripts and tests): each turn is written
      immediately in a worker thread.

    Persistence failures are logged and counted, never raised into the message flow.
    """

    def __init__(
        self,
        config: "ConversationLogConfig",
        backend: RegistryBackend,
        batch_size: int = 50,
        flush_interval: float = 5.0,
        max_buffer: int = 5_000,
    ):
        self._config = config
        self._backend = backend
        self._contacts = (
            ContactDirectory(config.contacts_file) if config.contacts_file else ContactDirectory()
        )
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._max_buffer = max_buffer
        self._queue: asyncio.Queue[ConversationRecord] | None = None
        self._writer: asyncio.Task | None = None

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._writer is None:
            self._queue = asyncio.Queue(maxsize=self._max_buffer)
            self._writer = asyncio.create_task(self._write_loop(), name="registry-writer")

    async def aclose(self, timeout: float = 10.0) -> None:
        """Flush buffered records and stop the writer."""
        if self._writer is not None:
            try:
                await asyncio.wait_for(self._queue.join(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.error("Registry flush timeout; %d record(s) lost", self._queue.qsize())
                metrics.REGISTRY_DROPPED.inc(self._queue.qsize())
            self._writer.cancel()
            await asyncio.gather(self._writer, return_exceptions=True)
            self._writer = None
        self.close()

    # ------------------------------------------------------------------ API
    async def log_turn(self, numero: str, pregunta: str, respuesta: str) -> None:
        try:
            record = self._build_record(numero, pregunta, respuesta)
        except Exception:
            logger.exception("Failed to build conversation record for %s", mask_phone(numero))
            return
        if self._queue is not None:
            try:
                self._queue.put_nowait(record)
            except asyncio.QueueFull:
                metrics.REGISTRY_DROPPED.inc()
                logger.error("Registry buffer full; dropping record for %s", mask_phone(numero))
            return
        try:
            await asyncio.to_thread(self._backend.log, record)
        except Exception:
            metrics.REGISTRY_DROPPED.inc()
            logger.exception("Failed to persist conversation record for %s", mask_phone(numero))

    # ------------------------------------------------------------- internals
    async def _write_loop(self) -> None:
        assert self._queue is not None
        loop = asyncio.get_running_loop()
        while True:
            batch = [await self._queue.get()]
            deadline = loop.time() + self._flush_interval
            while len(batch) < self._batch_size:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    batch.append(await asyncio.wait_for(self._queue.get(), timeout=remaining))
                except asyncio.TimeoutError:
                    break
            try:
                await asyncio.to_thread(self._backend.log_many, batch)
            except Exception:
                metrics.REGISTRY_DROPPED.inc(len(batch))
                logger.exception("Failed to persist %d conversation record(s)", len(batch))
            finally:
                for _ in batch:
                    self._queue.task_done()

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
