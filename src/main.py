"""WhatsApp chatbot entry point (FastAPI webhook server).

Endpoints
---------
GET  /webhook  Meta webhook verification (hub.challenge)
POST /webhook  inbound messages from Meta -> validated, deduplicated, queued
GET  /health   liveness probe (process is up)
GET  /ready    readiness probe (dependencies reachable, queue accepting)
GET  /metrics  Prometheus metrics (block it at the public proxy)

Request path (fast, < 50 ms): signature -> parse -> dedup(msg_id) -> rate
limit -> dispatcher.submit -> 200. The slow work (RAG + LLM + Meta send)
runs in the dispatcher's bounded worker pool (see src/dispatch).
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from src.agent.orchestrator import WhatsAppOrchestrator
from src.config import ConfigError, Settings, load_settings, validate_settings
from src.dispatch import Dispatcher, InboundMessage, build_dispatcher
from src.pipeline import MessageProcessor
from src.registry import ConversationRegistry, build_registry
from src.state import StateBundle, build_state
from src.utils import metrics
from src.utils.logging import setup_logging
from src.utils.pii import content_for_log, mask_phone
from src.utils.redis_client import build_redis

logger = logging.getLogger(__name__)

# Meta message types we answer with the "text only" notice. Reactions, system
# and unknown types are ignored silently (replying to a reaction is noise).
UNSUPPORTED_TYPES = {"image", "audio", "voice", "video", "document", "sticker", "location", "contacts"}


@dataclass
class AppComponents:
    """Everything the app talks to. Tests inject fakes here."""

    settings: Settings
    orchestrator: Any
    whatsapp: Any
    vector_store: Any
    qwen: Any
    registry: ConversationRegistry | None
    state: StateBundle
    dispatcher: Dispatcher
    redis: Any = None


def build_components(settings: Settings) -> AppComponents:
    from src.knowledge.embedding import build_embedder
    from src.knowledge.vector_store import VectorStore
    from src.llm.qwen import QwenClient
    from src.whatsapp.meta import MetaWhatsAppClient

    redis_client = build_redis(settings)
    state = build_state(settings, redis_client)
    vector_store = VectorStore(settings.storage.lancedb_path, settings.storage.table_name)
    embedder = build_embedder(
        settings.embeddings, llm_base_url=settings.llm.base_url,
        timeout_seconds=min(30.0, settings.llm.timeout_seconds), max_retries=settings.llm.max_retries,
    )
    qwen = QwenClient(settings.llm)
    return AppComponents(
        settings=settings,
        orchestrator=WhatsAppOrchestrator(settings, vector_store, embedder, qwen, state_store=state.store),
        whatsapp=MetaWhatsAppClient(settings.whatsapp),
        vector_store=vector_store,
        qwen=qwen,
        registry=build_registry(settings),
        state=state,
        dispatcher=build_dispatcher(settings, redis_client),
        redis=redis_client,
    )


def check_settings(settings: Settings) -> None:
    """Fail closed in production; warn loudly elsewhere."""
    problems = validate_settings(settings)
    if not problems:
        return
    if settings.is_production:
        raise ConfigError("Refusing to start in production:\n  - " + "\n  - ".join(problems))
    for problem in problems:
        logger.warning("Config (%s): %s", settings.runtime.environment, problem)


def create_app(settings: Settings | None = None, components: AppComponents | None = None) -> FastAPI:
    setup_logging()
    settings = settings or (components.settings if components else load_settings())
    check_settings(settings)
    c = components or build_components(settings)
    processor = MessageProcessor(settings, c.orchestrator, c.whatsapp, c.registry)
    ready_cache: dict[str, Any] = {"at": 0.0, "llm": False}

    metrics.QUEUE_PENDING.set_function(lambda: c.dispatcher.pending)
    metrics.QUEUE_INFLIGHT.set_function(lambda: c.dispatcher.inflight)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        logger.info(
            "Starting %s (%s). Knowledge chunks: %d. Model: %s. Queue: %s x%d. State: %s",
            settings.app.name, settings.runtime.environment, c.vector_store.count(), settings.llm.model,
            settings.queue.backend, settings.queue.workers, settings.state.backend,
        )
        if not c.whatsapp.signature_enabled:
            logger.warning("Webhook signature validation is DISABLED (set WHATSAPP_APP_SECRET)")
        if c.registry is not None:
            c.registry.start()
        await c.dispatcher.start(processor)
        yield
        logger.info("Shutting down: draining queue")
        await c.dispatcher.stop()
        if c.registry is not None:
            await c.registry.aclose()
        for closer in (getattr(c.qwen, "close", None), getattr(c.whatsapp, "close", None)):
            if closer is not None:
                await closer()
        if c.redis is not None:
            await c.redis.aclose()

    docs = {} if not settings.is_production else {"docs_url": None, "redoc_url": None, "openapi_url": None}
    app = FastAPI(title=settings.app.name, lifespan=lifespan, **docs)
    app.state.components = c

    # ---------------------------------------------------------------- routes
    @app.get("/webhook")
    async def webhook_verify(request: Request):
        params = request.query_params
        challenge = c.whatsapp.verify_webhook(
            params.get("hub.mode"),
            params.get("hub.verify_token"),
            params.get("hub.challenge"),
        )
        if challenge is None:
            return JSONResponse({"error": "Verification failed"}, status_code=403)
        return PlainTextResponse(challenge)

    @app.post("/webhook")
    async def webhook_receive(request: Request, background_tasks: BackgroundTasks):
        raw_body = await request.body()
        if c.whatsapp.signature_enabled:
            if not c.whatsapp.verify_signature(raw_body, request.headers.get("X-Hub-Signature-256")):
                logger.warning("Rejected webhook with invalid signature")
                return JSONResponse({"error": "Invalid signature"}, status_code=403)

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"error": "Invalid payload"}, status_code=400)

        for entry in payload.get("entry", []) or []:
            for change in entry.get("changes", []) or []:
                value = change.get("value", {}) or {}
                for message in value.get("messages", []) or []:
                    await _ingest(message, background_tasks)
        # Always 200 for well-formed, authentic payloads: a non-2xx makes Meta retry.
        return JSONResponse({"status": "received"})

    async def _ingest(message: dict, background_tasks: BackgroundTasks) -> None:
        phone = message.get("from")
        msg_id = message.get("id") or ""
        msg_type = message.get("type") or "unknown"
        if not phone:
            return
        text = extract_text(message)
        kind = "text" if text else ("unsupported" if msg_type in UNSUPPORTED_TYPES else "")
        metrics.MESSAGES_RECEIVED.labels(msg_type).inc()
        if not kind:
            return

        if msg_id and not await c.state.dedup.first_seen(msg_id):
            metrics.MESSAGES_DUPLICATE.inc()
            logger.info("Duplicate delivery ignored | phone=%s msg_id=%s", mask_phone(phone), msg_id)
            return

        rl = settings.rate_limit
        if rl.enabled:
            count = await c.state.limiter.hit(phone)
            if count > rl.max_messages:
                metrics.MESSAGES_REJECTED.labels("rate_limited").inc()
                if count == rl.max_messages + 1:  # notify once per window
                    background_tasks.add_task(_notify, phone, settings.agent.rate_limited_message)
                logger.warning("Rate limited | phone=%s count=%d", mask_phone(phone), count)
                return

        text = (text or "")[: rl.max_input_chars]
        logger.info(
            "Inbound | phone=%s msg_id=%s type=%s text=%s",
            mask_phone(phone), msg_id or "-", msg_type, content_for_log(text),
        )
        result = await c.dispatcher.submit(
            InboundMessage(phone=phone, text=text, msg_id=msg_id, kind=kind, received_at=time.time())
        )
        if not result.accepted:
            metrics.MESSAGES_REJECTED.labels(result.reason or "full").inc()
            logger.error("Queue rejected message (%s) | phone=%s pending=%d",
                         result.reason, mask_phone(phone), result.pending)
            background_tasks.add_task(_notify, phone, settings.agent.busy_message)
            return
        threshold = settings.queue.ack_when_pending_over
        if threshold and result.pending > threshold and settings.agent.queued_message:
            # At most one "we got your message" notice per customer every 5 minutes.
            window = int(time.time() // 300)
            if await c.state.dedup.first_seen(f"ack:{phone}:{window}"):
                background_tasks.add_task(_notify, phone, settings.agent.queued_message)

    async def _notify(phone: str, text: str) -> None:
        try:
            await c.whatsapp.send_text(phone, text)
        except Exception:
            logger.warning("Could not send notice to %s", mask_phone(phone), exc_info=True)

    @app.get("/health")
    async def health():
        # Liveness only (cheap, no network). Dependencies are checked in /ready.
        try:
            chunks = c.vector_store.count()
        except Exception:
            chunks = -1
        return {"status": "ok", "chunks": chunks}

    @app.get("/ready")
    async def ready():
        checks: dict[str, bool] = {"queue": c.dispatcher.running}
        try:
            checks["knowledge"] = c.vector_store.count() > 0
        except Exception:
            checks["knowledge"] = False
        now = time.monotonic()
        if now - ready_cache["at"] > 15:  # do not hammer the LLM with probes
            ping = getattr(c.qwen, "ping", None)
            ready_cache["llm"] = (await ping()) if ping else True
            ready_cache["at"] = now
        checks["llm"] = ready_cache["llm"]
        breaker = getattr(c.qwen, "breaker", None)
        checks["llm_circuit_closed"] = breaker is None or not breaker.is_open
        if c.redis is not None:
            try:
                checks["redis"] = bool(await c.redis.ping())
            except Exception:
                checks["redis"] = False
        if settings.is_production:
            checks["whatsapp_configured"] = bool(c.whatsapp.configured)
        ok = all(checks.values())
        body = {"status": "ready" if ok else "not_ready", "checks": checks,
                "queue": {"pending": c.dispatcher.pending, "inflight": c.dispatcher.inflight}}
        return JSONResponse(body, status_code=200 if ok else 503)

    @app.get("/metrics")
    async def metrics_endpoint():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


def extract_text(message: dict) -> str | None:
    msg_type = message.get("type")
    if msg_type == "text":
        return (message.get("text") or {}).get("body")
    if msg_type == "interactive":
        interactive = message.get("interactive") or {}
        if interactive.get("type") == "button_reply":
            return (interactive.get("button_reply") or {}).get("title")
        if interactive.get("type") == "list_reply":
            return (interactive.get("list_reply") or {}).get("title")
    if msg_type == "button":  # quick-reply button on a template message
        return (message.get("button") or {}).get("text")
    return None


def __getattr__(name: str):
    # ``uvicorn src.main:app`` resolves the app lazily, so importing this module
    # (tests, scripts) does not build clients or require configuration.
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
