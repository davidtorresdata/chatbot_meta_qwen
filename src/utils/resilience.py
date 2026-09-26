"""Resilience primitives: circuit breaker and retry backoff."""

from __future__ import annotations

import logging
import random
import time

logger = logging.getLogger(__name__)


class CircuitOpenError(RuntimeError):
    """The downstream dependency is failing; the call was not attempted."""


class CircuitBreaker:
    """Consecutive-failure circuit breaker (closed -> open -> half-open -> closed).

    * closed: calls pass; ``failure_threshold`` consecutive failures open it.
    * open: calls fail fast with :class:`CircuitOpenError` for ``cooldown`` s,
      so a dead LLM does not make every queued message wait for its timeout.
    * half-open: after the cooldown one trial call is let through; success
      closes the circuit, failure re-opens it.
    """

    def __init__(self, name: str, failure_threshold: int = 5, cooldown_seconds: float = 30.0,
                 clock=time.monotonic):
        self.name = name
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._trial_in_flight = False

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self._cooldown:
            return "half_open"
        return "open"

    @property
    def is_open(self) -> bool:
        return self.state == "open"

    def before_call(self) -> None:
        state = self.state
        if state == "open":
            raise CircuitOpenError(f"circuit '{self.name}' is open")
        if state == "half_open":
            if self._trial_in_flight:
                raise CircuitOpenError(f"circuit '{self.name}' is half-open (trial in flight)")
            self._trial_in_flight = True

    def record_success(self) -> None:
        if self._opened_at is not None:
            logger.info("Circuit '%s' closed", self.name)
        self._failures = 0
        self._opened_at = None
        self._trial_in_flight = False

    def record_failure(self) -> None:
        self._trial_in_flight = False
        self._failures += 1
        if self._opened_at is not None or self._failures >= self._threshold:
            if self._opened_at is None:
                logger.error("Circuit '%s' opened after %d consecutive failures", self.name, self._failures)
            self._opened_at = self._clock()


def backoff_delay(attempt: int, base: float = 0.5, cap: float = 8.0) -> float:
    """Exponential backoff with full jitter (attempt starts at 0)."""
    return random.uniform(0, min(cap, base * (2 ** attempt)))
