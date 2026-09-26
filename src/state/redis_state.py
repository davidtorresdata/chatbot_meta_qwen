"""Redis state backend (shared across replicas, survives restarts)."""

from __future__ import annotations

import json
import time
from typing import Any


class RedisStateStore:
    def __init__(self, client, prefix: str, ttl_seconds: int):
        self._r = client
        self._prefix = prefix
        self._ttl = ttl_seconds

    def _key(self, phone: str) -> str:
        return f"{self._prefix}:conv:{phone}"

    async def get(self, phone: str) -> dict[str, Any] | None:
        raw = await self._r.get(self._key(phone))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    async def set(self, phone: str, snapshot: dict[str, Any]) -> None:
        await self._r.set(self._key(phone), json.dumps(snapshot, ensure_ascii=False), ex=self._ttl)

    async def delete(self, phone: str) -> None:
        await self._r.delete(self._key(phone))


class RedisDeduplicator:
    def __init__(self, client, prefix: str, ttl_seconds: int, namespace: str = "seen"):
        self._r = client
        self._prefix = prefix
        self._ttl = ttl_seconds
        self._ns = namespace

    async def first_seen(self, key: str) -> bool:
        # SET NX EX is atomic: exactly one replica wins for a given message id.
        return bool(await self._r.set(f"{self._prefix}:{self._ns}:{key}", "1", nx=True, ex=self._ttl))


class RedisRateLimiter:
    def __init__(self, client, prefix: str, window_seconds: int):
        self._r = client
        self._prefix = prefix
        self._window = window_seconds

    async def hit(self, phone: str) -> int:
        bucket = int(time.time() // self._window)
        key = f"{self._prefix}:rl:{phone}:{bucket}"
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, self._window * 2)
            count, _ = await pipe.execute()
        return int(count)
