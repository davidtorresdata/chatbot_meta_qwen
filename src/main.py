"""WhatsApp chatbot entry point (FastAPI webhook server).

Endpoints
---------
GET  /webhook  Meta webhook verification (hub.challenge)
POST /webhook  inbound messages from Meta
GET  /health   liveness probe
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from src.agent.orchestrator import ReplyAction, WhatsAppOrchestrator
from src.config import load_settings
from src.knowledge.embedding import build_embedder
from src.knowledge.vector_store import VectorStore
from src.llm.qwen import QwenClient
from src.registry import ConversationRegistry, build_registry
from src.utils.logging import setup_logging
from src.whatsapp.meta import MetaWhatsAppClient

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    setup_logging()
    settings = load_settings()

    vector_store = VectorStore(settings.storage.lancedb_path, settings.storage.table_name)
    embedder = build_embedder(settings.embeddings, llm_base_url=settings.llm.base_url)
    qwen = QwenClient(settings.llm)
    whatsapp = MetaWhatsAppClient(settings.whatsapp)
    orchestrator = WhatsAppOrchestrator(settings, vector_store, embedder, qwen)
    registry = build_registry(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        logger.info(
            "Starting %s. Knowledge chunks: %d. Qwen: %s",
            settings.app.name,
            vector_store.count(),
            settings.llm.model,
        )
        logger.info(
            "Conversation registry: %s",
            "disabled" if registry is None else f"{settings.conversation_log.backend} backend",
        )
        yield
        if registry is not None:
            registry.close()
        await qwen.close()
        await whatsapp.close()

    app = FastAPI(title=settings.app.name, lifespan=lifespan)

    # ---------------------------------------------------------------- routes
    @app.get("/webhook")
    async def webhook_verify(request: Request):
        params = request.query_params
        challenge = whatsapp.verify_webhook(
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
        if whatsapp.signature_enabled:
            if not whatsapp.verify_signature(raw_body, request.headers.get("X-Hub-Signature-256")):
                logger.warning("Rejected webhook with invalid signature")
                return JSONResponse({"error": "Invalid signature"}, status_code=403)
        else:
            logger.warning("Webhook signature validation is DISABLED (set WHATSAPP_APP_SECRET)")

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for message in value.get("messages", []):
                    phone = message.get("from")
                    msg_id = message.get("id")
                    text = extract_text(message)
                    if phone and text:
                        logger.info("Inbound action | phone=%s msg_id=%s text=%r", phone, msg_id, text)
                        background_tasks.add_task(
                            _handle_message, orchestrator, whatsapp, registry, phone, text, msg_id
                        )
        return JSONResponse({"status": "received"})

    @app.get("/health")
    async def health():
        return {"status": "ok", "chunks": vector_store.count()}

    return app


def extract_text(message: dict) -> str | None:
    msg_type = message.get("type")
    if msg_type == "text":
        return message.get("text", {}).get("body")
    if msg_type == "interactive":
        interactive = message.get("interactive", {})
        if interactive.get("type") == "button_reply":
            return interactive.get("button_reply", {}).get("title")
        if interactive.get("type") == "list_reply":
            return interactive.get("list_reply", {}).get("title")
    return None


async def _handle_message(
    orchestrator: WhatsAppOrchestrator,
    whatsapp: MetaWhatsAppClient,
    registry: ConversationRegistry | None,
    phone: str,
    text: str,
    msg_id: str | None,
) -> None:
    try:
        action: ReplyAction = await orchestrator.handle_message(phone, text)
        logger.info("Action computed | phone=%s type=%s message=%r url=%s", phone, action.type, action.message, action.url)
        if msg_id:
            await whatsapp.mark_read(msg_id)
        await _send_action(whatsapp, phone, action)
        await _log_turn(registry, phone, text, action.message)
    except Exception:
        logger.exception("Failed to process message from %s", phone)
        try:
            await whatsapp.send_text(phone, orchestrator.fallback_message)
        except Exception:
            logger.exception("Failed to send fallback to %s", phone)
        await _log_turn(registry, phone, text, orchestrator.fallback_message)


async def _log_turn(
    registry: ConversationRegistry | None,
    phone: str,
    pregunta: str,
    respuesta: str,
) -> None:
    if registry is not None:
        await registry.log_turn(phone, pregunta, respuesta)


async def _send_action(whatsapp: MetaWhatsAppClient, phone: str, action: ReplyAction) -> None:
    if action.type == "redirect" and action.url:
        logger.info("Action sent | phone=%s kind=redirect url=%s", phone, action.url)
        await whatsapp.send_cta_url(phone, action.message, action.url)
    else:
        logger.info("Action sent | phone=%s kind=text", phone)
        await whatsapp.send_text(phone, action.message)


app = create_app()
