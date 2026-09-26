"""Shared state: conversation snapshots, message dedup and per-phone rate limits.

Two interchangeable backends selected by ``STATE_BACKEND``:

* ``memory`` - in-process, bounded (LRU) and expiring (TTL). Single instance only.
* ``redis``  - shared by every replica, survives restarts. Needed to scale out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.state.base import Deduplicator, RateLimiter, StateStore
from src.state.memory import MemoryDeduplicator, MemoryRateLimiter, MemoryStateStore

if TYPE_CHECKING:
    from src.config import Settings

__all__ = [
    "Deduplicator", "RateLimiter", "StateStore", "StateBundle", "build_state",
    "MemoryDeduplicator", "MemoryRateLimiter", "MemoryStateStore",
]


@dataclass
class StateBundle:
    store: StateStore
    dedup: Deduplicator
    limiter: RateLimiter


def build_state(settings: "Settings", redis_client=None) -> StateBundle:
    cfg = settings.state
    backend = cfg.backend.strip().lower()
    if backend == "redis":
        if redis_client is None:
            raise ValueError("STATE_BACKEND=redis requires REDIS_URL")
        from src.state.redis_state import RedisDeduplicator, RedisRateLimiter, RedisStateStore

        prefix = settings.runtime.redis_prefix
        return StateBundle(
            store=RedisStateStore(redis_client, prefix, cfg.conversation_ttl_seconds),
            dedup=RedisDeduplicator(redis_client, prefix, cfg.dedup_ttl_seconds),
            limiter=RedisRateLimiter(redis_client, prefix, settings.rate_limit.window_seconds),
        )
    return StateBundle(
        store=MemoryStateStore(cfg.conversation_ttl_seconds, cfg.max_conversations),
        dedup=MemoryDeduplicator(cfg.dedup_ttl_seconds),
        limiter=MemoryRateLimiter(settings.rate_limit.window_seconds),
    )
