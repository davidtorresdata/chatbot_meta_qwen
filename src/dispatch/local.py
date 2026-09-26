"""In-process queue (single instance). Zero infrastructure.

Messages live in memory: a hard crash loses the backlog. A graceful shutdown
(SIGTERM from Docker) stops intake and drains within ``drain_timeout``.
Use the Redis dispatcher for durability or more than one replica.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque

from src.dispatch.base import Dispatcher, Handler, InboundMessage, SubmitResult
from src.utils.pii import mask_phone

logger = logging.getLogger(__name__)


class LocalDispatcher(Dispatcher):
    def __init__(self, workers: int = 2, max_pending: int = 500, drain_timeout: float = 25.0):
        self._workers = workers
        self._max_pending = max_pending
        self._drain_timeout = drain_timeout
        self._mailboxes: dict[str, deque[InboundMessage]] = {}
        self._scheduled: set[str] = set()  # phones in the ready line or being processed
        self._ready: asyncio.Queue[str] | None = None
        self._tasks: list[asyncio.Task] = []
        self._handler: Handler | None = None
        self._pending = 0
        self._inflight = 0
        self._accepting = False
        self._idle: asyncio.Event | None = None

    # ------------------------------------------------------------------ API
    async def start(self, handler: Handler) -> None:
        self._handler = handler
        self._ready = asyncio.Queue()
        self._idle = asyncio.Event()
        self._idle.set()
        self._accepting = True
        self._tasks = [
            asyncio.create_task(self._worker(i), name=f"dispatch-worker-{i}") for i in range(self._workers)
        ]
        logger.info("Local dispatcher started: workers=%d max_pending=%d", self._workers, self._max_pending)

    async def submit(self, message: InboundMessage) -> SubmitResult:
        if not self._accepting:
            return SubmitResult(False, self._pending, "stopping")
        if self._pending >= self._max_pending:
            return SubmitResult(False, self._pending, "full")
        # No await below: the whole enqueue is atomic within the event loop.
        self._mailboxes.setdefault(message.phone, deque()).append(message)
        self._pending += 1
        self._idle.clear()
        if message.phone not in self._scheduled:
            self._scheduled.add(message.phone)
            self._ready.put_nowait(message.phone)
        return SubmitResult(True, self._pending)

    async def stop(self) -> None:
        self._accepting = False
        if self._idle is not None and self._pending:
            logger.info("Draining %d pending message(s) (budget %.0fs)", self._pending, self._drain_timeout)
            try:
                await asyncio.wait_for(self._idle.wait(), timeout=self._drain_timeout)
            except asyncio.TimeoutError:
                logger.error("Drain timeout: %d message(s) not processed", self._pending)
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    @property
    def pending(self) -> int:
        return self._pending

    @property
    def inflight(self) -> int:
        return self._inflight

    @property
    def running(self) -> bool:
        return self._accepting and any(not t.done() for t in self._tasks)

    # ------------------------------------------------------------- internals
    async def _worker(self, index: int) -> None:
        assert self._ready is not None and self._handler is not None
        while True:
            phone = await self._ready.get()
            mailbox = self._mailboxes.get(phone)
            if not mailbox:  # defensive: nothing left for this phone
                self._release(phone)
                continue
            message = mailbox.popleft()
            self._inflight += 1
            try:
                await self._handler(message)
            except asyncio.CancelledError:
                raise
            except Exception:  # the handler owns user-facing fallbacks; never kill a worker
                logger.exception("Worker %d: unhandled error for %s", index, mask_phone(phone))
            finally:
                self._inflight -= 1
                self._pending -= 1
                if mailbox:
                    self._ready.put_nowait(phone)  # more for this phone: back of the line
                else:
                    self._release(phone)
                if self._pending == 0:
                    self._idle.set()

    def _release(self, phone: str) -> None:
        self._scheduled.discard(phone)
        mailbox = self._mailboxes.get(phone)
        if mailbox is not None and not mailbox:
            del self._mailboxes[phone]
