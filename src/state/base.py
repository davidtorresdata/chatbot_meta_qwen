"""State contracts shared by the memory and Redis backends."""

from __future__ import annotations

from typing import Any, Protocol


class StateStore(Protocol):
    """Per-phone conversation snapshot (history + tree session) with idle TTL."""

    async def get(self, phone: str) -> dict[str, Any] | None: ...

    async def set(self, phone: str, snapshot: dict[str, Any]) -> None: ...

    async def delete(self, phone: str) -> None: ...


class Deduplicator(Protocol):
    async def first_seen(self, key: str) -> bool:
        """True the first time ``key`` is seen within the TTL, False afterwards."""
        ...


class RateLimiter(Protocol):
    async def hit(self, phone: str) -> int:
        """Register one message and return how many were seen in the current window."""
        ...
