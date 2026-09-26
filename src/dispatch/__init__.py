"""Inbound message queue. See :mod:`src.dispatch.base` for the design."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.dispatch.base import Dispatcher, InboundMessage, SubmitResult
from src.dispatch.local import LocalDispatcher

if TYPE_CHECKING:
    from src.config import Settings

__all__ = ["Dispatcher", "InboundMessage", "SubmitResult", "LocalDispatcher", "build_dispatcher"]


def build_dispatcher(settings: "Settings", redis_client=None) -> Dispatcher:
    q = settings.queue
    if q.backend.strip().lower() == "redis":
        if redis_client is None:
            raise ValueError("QUEUE_BACKEND=redis requires REDIS_URL")
        from src.dispatch.redis_dispatcher import RedisDispatcher

        return RedisDispatcher(
            redis_client,
            prefix=settings.runtime.redis_prefix,
            workers=q.workers,
            max_pending=q.max_pending,
            drain_timeout=q.drain_timeout_seconds,
            heartbeat_seconds=q.heartbeat_seconds,
            orphan_after_seconds=q.orphan_after_seconds,
            done_ttl_seconds=settings.state.dedup_ttl_seconds,
        )
    return LocalDispatcher(q.workers, q.max_pending, q.drain_timeout_seconds)
