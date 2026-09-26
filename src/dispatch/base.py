"""Queue contracts.

Design: *mailbox per phone*. Every phone number has its own FIFO mailbox and at
most one worker processes a given phone at a time, so

* messages of one conversation are handled strictly in order (no races on the
  tree session or chat history), while
* different conversations run in parallel, bounded by ``workers``, and
* after each message the phone goes to the back of the ready line (fairness:
  a chatty user cannot starve others).

``max_pending`` bounds the total backlog (queued + in flight). When it is
reached ``submit`` rejects instead of queueing without limit (backpressure).
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable


@dataclass
class InboundMessage:
    phone: str
    text: str
    msg_id: str = ""
    kind: str = "text"  # text | unsupported
    received_at: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str | bytes) -> "InboundMessage":
        data = json.loads(raw)
        return cls(**{k: data[k] for k in ("phone", "text", "msg_id", "kind", "received_at") if k in data})


@dataclass
class SubmitResult:
    accepted: bool
    pending: int = 0  # backlog size right after this submit
    reason: str = ""  # full | stopping


Handler = Callable[[InboundMessage], Awaitable[None]]


class Dispatcher(ABC):
    @abstractmethod
    async def start(self, handler: Handler) -> None: ...

    @abstractmethod
    async def submit(self, message: InboundMessage) -> SubmitResult: ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop accepting, drain within the configured budget, stop workers."""

    @property
    @abstractmethod
    def pending(self) -> int:
        """Backlog (queued + in flight). Redis backend: last observed value."""

    @property
    @abstractmethod
    def inflight(self) -> int:
        """Messages being processed by this instance."""

    @property
    @abstractmethod
    def running(self) -> bool: ...
