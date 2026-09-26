"""In-process state backend: bounded (LRU) and expiring (TTL).

Safe inside one asyncio event loop (no awaits inside critical sections).
Not shared between processes/replicas - use the Redis backend to scale out.
"""

from __future__ import annotations

import copy
import time
from collections import OrderedDict
from typing import Any, Callable


class TTLCache:
    """OrderedDict-based LRU with per-entry expiry."""

    def __init__(self, ttl_seconds: float, max_items: int, clock: Callable[[], float] = time.monotonic):
        self._ttl = ttl_seconds
        self._max = max_items
        self._clock = clock
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if expires <= self._clock():
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        self._data[key] = (self._clock() + (ttl or self._ttl), value)
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

    def __len__(self) -> int:
        return len(self._data)


class MemoryStateStore:
    def __init__(self, ttl_seconds: int = 1800, max_items: int = 10_000, clock=time.monotonic):
        self._cache = TTLCache(ttl_seconds, max_items, clock)

    async def get(self, phone: str) -> dict[str, Any] | None:
        value = self._cache.get(phone)
        return copy.deepcopy(value) if value is not None else None

    async def set(self, phone: str, snapshot: dict[str, Any]) -> None:
        self._cache.set(phone, copy.deepcopy(snapshot))

    async def delete(self, phone: str) -> None:
        self._cache.delete(phone)

    def __len__(self) -> int:
        return len(self._cache)


class MemoryDeduplicator:
    def __init__(self, ttl_seconds: int = 86_400, max_items: int = 200_000, clock=time.monotonic):
        self._cache = TTLCache(ttl_seconds, max_items, clock)

    async def first_seen(self, key: str) -> bool:
        if key in self._cache:
            return False
        self._cache.set(key, True)
        return True


class MemoryRateLimiter:
    """Fixed-window counter per phone."""

    def __init__(self, window_seconds: int = 60, max_items: int = 100_000, clock=time.monotonic):
        self._window = window_seconds
        self._clock = clock
        self._cache = TTLCache(window_seconds, max_items, clock)

    async def hit(self, phone: str) -> int:
        bucket = int(self._clock() // self._window)
        key = f"{phone}:{bucket}"
        count = (self._cache.get(key) or 0) + 1
        self._cache.set(key, count)
        return count
