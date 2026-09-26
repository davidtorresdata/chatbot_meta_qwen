"""Prometheus metrics (exposed on GET /metrics, scraped by Prometheus).

Metric names are stable contracts for dashboards/alerts - do not rename lightly.
Defined once at import time on the default registry.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

_LATENCY_BUCKETS = (0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 90, 120, 180)

MESSAGES_RECEIVED = Counter(
    "metabot_messages_received_total", "Inbound WhatsApp messages by type", ["kind"]
)
MESSAGES_DUPLICATE = Counter(
    "metabot_messages_duplicate_total", "Inbound messages dropped as Meta redeliveries"
)
MESSAGES_REJECTED = Counter(
    "metabot_messages_rejected_total", "Inbound messages not queued", ["reason"]
)
MESSAGES_PROCESSED = Counter(
    "metabot_messages_processed_total", "Messages processed by action type / outcome",
    ["action", "outcome"],
)
PROCESSING_SECONDS = Histogram(
    "metabot_processing_seconds", "End-to-end processing time per message (worker)",
    buckets=_LATENCY_BUCKETS,
)
QUEUE_WAIT_SECONDS = Histogram(
    "metabot_queue_wait_seconds", "Time a message waited in the queue before a worker took it",
    buckets=_LATENCY_BUCKETS,
)
QUEUE_PENDING = Gauge("metabot_queue_pending", "Messages queued or in flight")
QUEUE_INFLIGHT = Gauge("metabot_queue_inflight", "Messages being processed by this instance")
LLM_SECONDS = Histogram("metabot_llm_seconds", "LLM completion latency", buckets=_LATENCY_BUCKETS)
LLM_ERRORS = Counter("metabot_llm_errors_total", "LLM call failures", ["reason"])
CIRCUIT_OPEN = Gauge("metabot_circuit_open", "1 when the circuit breaker is open", ["name"])
META_SEND_ERRORS = Counter(
    "metabot_meta_send_errors_total", "Meta Graph API send failures", ["status"]
)
REGISTRY_DROPPED = Counter(
    "metabot_registry_dropped_total", "Conversation records not persisted"
)
