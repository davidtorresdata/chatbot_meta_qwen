"""Redis-backed queue: durable backlog, shared by any number of replicas.

Same *mailbox per phone* semantics as the local dispatcher, implemented with
atomic Lua scripts so several replicas can consume safely.

Keys (``P`` = prefix):

* ``P:q:mbox:<phone>``   list  - FIFO mailbox of serialized messages
* ``P:q:sched:<phone>``  str   - phone is in the ready line or owned by a worker
* ``P:q:ready``          list  - phones with work, consumed with BLMOVE
* ``P:q:proc:<worker>``  list  - phone currently owned by a worker (reliable queue)
* ``P:q:workers``        zset  - worker heartbeats (score = unix time)
* ``P:q:pending``        str   - backlog counter used for backpressure
* ``P:q:done:<msg_id>``  str   - processed marker (suppresses replays after a crash)

Delivery is at-least-once: the message is removed from its mailbox only after
the handler finished. If a replica dies mid-message, its heartbeat expires and
another replica moves the phone back to the ready line (``orphan_after``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
import uuid

from src.dispatch.base import Dispatcher, Handler, InboundMessage, SubmitResult
from src.utils.pii import mask_phone

logger = logging.getLogger(__name__)

# KEYS: pending, mbox, sched, ready   ARGV: max_pending, payload, phone
_SUBMIT = """
local n = tonumber(redis.call('GET', KEYS[1]) or '0')
if n >= tonumber(ARGV[1]) then return -1 end
redis.call('RPUSH', KEYS[2], ARGV[2])
local p = redis.call('INCR', KEYS[1])
if redis.call('SET', KEYS[3], '1', 'NX') then
  redis.call('RPUSH', KEYS[4], ARGV[3])
end
return p
"""

# KEYS: mbox, pending, sched, ready, proc   ARGV: phone, consumed(0|1)
_FINISH = """
if ARGV[2] == '1' then
  if redis.call('LPOP', KEYS[1]) then
    if tonumber(redis.call('DECR', KEYS[2])) < 0 then redis.call('SET', KEYS[2], '0') end
  end
end
if redis.call('LLEN', KEYS[1]) > 0 then
  redis.call('RPUSH', KEYS[4], ARGV[1])
else
  redis.call('DEL', KEYS[3])
end
redis.call('LREM', KEYS[5], 1, ARGV[1])
return 1
"""

# KEYS: proc(dead worker), ready, workers   ARGV: worker_id
_REQUEUE_DEAD = """
local moved = 0
while true do
  local phone = redis.call('LMOVE', KEYS[1], KEYS[2], 'RIGHT', 'LEFT')
  if not phone then break end
  moved = moved + 1
end
redis.call('ZREM', KEYS[3], ARGV[1])
return moved
"""


class RedisDispatcher(Dispatcher):
    def __init__(
        self,
        client,
        prefix: str = "metabot",
        workers: int = 2,
        max_pending: int = 500,
        drain_timeout: float = 25.0,
        heartbeat_seconds: float = 5.0,
        orphan_after_seconds: float = 60.0,
        done_ttl_seconds: int = 86_400,
        poll_timeout: float = 1.0,
    ):
        self._r = client
        self._p = f"{prefix}:q"
        self._workers = workers
        self._max_pending = max_pending
        self._drain_timeout = drain_timeout
        self._hb = heartbeat_seconds
        self._orphan_after = orphan_after_seconds
        self._done_ttl = done_ttl_seconds
        self._poll = poll_timeout
        self._id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self._handler: Handler | None = None
        self._tasks: list[asyncio.Task] = []
        self._maint: asyncio.Task | None = None
        self._accepting = False
        self._stopping = False
        self._inflight = 0
        self._pending_cache = 0
        self._submit = client.register_script(_SUBMIT)
        self._finish = client.register_script(_FINISH)
        self._requeue_dead = client.register_script(_REQUEUE_DEAD)

    # ------------------------------------------------------------------ keys
    def _k(self, *parts: str) -> str:
        return ":".join((self._p, *parts))

    def _proc_key(self, slot: int) -> str:
        return self._k("proc", f"{self._id}#{slot}")

    # ------------------------------------------------------------------ API
    async def start(self, handler: Handler) -> None:
        self._handler = handler
        self._accepting = True
        self._stopping = False
        await self._heartbeat()
        await self._reap_orphans()
        self._tasks = [asyncio.create_task(self._worker(i), name=f"redis-worker-{i}") for i in range(self._workers)]
        self._maint = asyncio.create_task(self._maintenance(), name="redis-dispatch-maintenance")
        logger.info("Redis dispatcher %s started: workers=%d max_pending=%d", self._id, self._workers, self._max_pending)

    async def submit(self, message: InboundMessage) -> SubmitResult:
        if not self._accepting:
            return SubmitResult(False, self._pending_cache, "stopping")
        result = await self._submit(
            keys=[self._k("pending"), self._k("mbox", message.phone), self._k("sched", message.phone), self._k("ready")],
            args=[self._max_pending, message.to_json(), message.phone],
        )
        result = int(result)
        if result < 0:
            return SubmitResult(False, self._max_pending, "full")
        self._pending_cache = result
        return SubmitResult(True, result)

    async def stop(self) -> None:
        self._accepting = False
        self._stopping = True  # workers finish the current message and exit
        if self._tasks:
            done, not_done = await asyncio.wait(self._tasks, timeout=self._drain_timeout)
            for task in not_done:
                task.cancel()  # the phone stays in its proc list -> requeued as orphan
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._maint:
            self._maint.cancel()
            await asyncio.gather(self._maint, return_exceptions=True)
        # Hand back anything still owned by this instance, then deregister.
        for slot in range(self._workers):
            try:
                await self._requeue_dead(keys=[self._proc_key(slot), self._k("ready"), self._k("workers")],
                                         args=[f"{self._id}#{slot}"])
            except Exception:
                logger.warning("Could not release slot %d on shutdown", slot, exc_info=True)
        self._tasks = []

    @property
    def pending(self) -> int:
        return self._pending_cache

    @property
    def inflight(self) -> int:
        return self._inflight

    @property
    def running(self) -> bool:
        return self._accepting and any(not t.done() for t in self._tasks)

    # ------------------------------------------------------------- internals
    async def _worker(self, slot: int) -> None:
        proc = self._proc_key(slot)
        while not self._stopping:
            try:
                phone = await self._r.blmove(self._k("ready"), proc, self._poll, "LEFT", "RIGHT")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Redis dispatcher: BLMOVE failed; retrying")
                await asyncio.sleep(1)
                continue
            if phone is None:
                continue
            phone = phone.decode() if isinstance(phone, bytes) else phone
            await self._process_phone(phone, proc)

    async def _process_phone(self, phone: str, proc: str) -> None:
        mbox = self._k("mbox", phone)
        consumed = "0"
        raw = await self._r.lindex(mbox, 0)
        if raw is not None:
            consumed = "1"
            try:
                message = InboundMessage.from_json(raw)
            except Exception:
                logger.error("Dropping malformed queued message for %s", mask_phone(phone))
                message = None
            if message is not None and await self._not_done(message):
                self._inflight += 1
                try:
                    await self._handler(message)
                    if message.msg_id:
                        await self._r.set(self._k("done", message.msg_id), "1", ex=self._done_ttl)
                except asyncio.CancelledError:
                    self._inflight -= 1
                    raise  # leave it in proc/mailbox: requeued as orphan, at-least-once
                except Exception:
                    logger.exception("Redis worker: unhandled error for %s", mask_phone(phone))
                self._inflight -= 1
        await self._finish(
            keys=[mbox, self._k("pending"), self._k("sched", phone), self._k("ready"), proc],
            args=[phone, consumed],
        )

    async def _not_done(self, message: InboundMessage) -> bool:
        if not message.msg_id:
            return True
        return not await self._r.exists(self._k("done", message.msg_id))

    async def _heartbeat(self) -> None:
        now = time.time()
        await self._r.zadd(self._k("workers"), {f"{self._id}#{slot}": now for slot in range(self._workers)})
        raw = await self._r.get(self._k("pending"))
        self._pending_cache = int(raw or 0)

    async def _reap_orphans(self) -> None:
        cutoff = time.time() - self._orphan_after
        dead = await self._r.zrangebyscore(self._k("workers"), "-inf", cutoff)
        for member in dead:
            member = member.decode() if isinstance(member, bytes) else member
            moved = await self._requeue_dead(
                keys=[self._k("proc", member), self._k("ready"), self._k("workers")], args=[member]
            )
            if moved:
                logger.warning("Requeued %d conversation(s) from dead worker %s", moved, member)

    async def _maintenance(self) -> None:
        while True:
            await asyncio.sleep(self._hb)
            try:
                await self._heartbeat()
                await self._reap_orphans()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Redis dispatcher maintenance failed", exc_info=True)
