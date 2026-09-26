"""Shared async Redis client (only built when a Redis backend is configured)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import Settings


def needs_redis(settings: "Settings") -> bool:
    return "redis" in (settings.queue.backend.strip().lower(), settings.state.backend.strip().lower())


def build_redis(settings: "Settings"):
    if not needs_redis(settings):
        return None
    if not settings.runtime.redis_url:
        raise ValueError("A redis backend is configured but REDIS_URL is empty")
    import redis.asyncio as redis

    return redis.from_url(
        settings.runtime.redis_url,
        decode_responses=True,
        health_check_interval=30,
        socket_connect_timeout=5,
        socket_timeout=10,
        retry_on_timeout=True,
    )
