"""End-to-end webhook -> queue -> worker -> reply, with fakes (no LLM / Meta)."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from src.config import ConfigError, Settings
from src.dispatch import LocalDispatcher
from src.main import AppComponents, create_app
from src.state import build_state
from src.utils.resilience import CircuitOpenError
from tests.conftest import (
    FakeOrchestrator, FakeQwen, FakeVectorStore, FakeWhatsApp, make_payload, text_msg, wait_until,
)


def build(settings: Settings | None = None, orchestrator=None, workers=3, max_pending=500):
    settings = settings or Settings()
    settings.tree.enabled = False
    wa = FakeWhatsApp()
    orch = orchestrator or FakeOrchestrator()
    comps = AppComponents(
        settings=settings, orchestrator=orch, whatsapp=wa, vector_store=FakeVectorStore(),
        qwen=FakeQwen(), registry=None, state=build_state(settings),
        dispatcher=LocalDispatcher(workers=workers, max_pending=max_pending, drain_timeout=10),
    )
    return create_app(components=comps), wa, orch


def replies(wa, phone=None):
    return [body for to, body in wa.sent if phone is None or to == phone]


def test_concurrent_conversations_keep_order_and_bounded_parallelism():
    settings = Settings()
    app, wa, orch = build(settings, workers=3)
    phones = [f"5730000000{i:02d}" for i in range(12)]
    with TestClient(app) as client:
        for n in range(5):
            for phone in phones:
                r = client.post("/webhook", json=make_payload(text_msg(phone, f"{phone}-{n}", f"m{n}")))
                assert r.status_code == 200
        assert wait_until(lambda: sum(b.startswith("eco:") for b in replies(wa)) == 60)
    assert orch.overlaps == 0
    assert 1 < orch.max_active <= 3
    for phone in phones:
        assert orch.seen[phone] == [f"m{n}" for n in range(5)]
        assert [b for b in replies(wa, phone) if b.startswith("eco:")] == [f"eco:m{n}" for n in range(5)]
    # backlog > ack_when_pending_over (10) -> some customers were told to wait
    assert settings.agent.queued_message in replies(wa)


def test_meta_redelivery_is_processed_once():
    app, wa, orch = build()
    with TestClient(app) as client:
        payload = make_payload(text_msg("573001", "wamid.X", "hola"))
        for _ in range(3):
            client.post("/webhook", json=payload)
        assert wait_until(lambda: len(wa.sent) >= 1)
        wait_until(lambda: False, timeout=0.2)
    assert orch.seen["573001"] == ["hola"]
    assert len(wa.sent) == 1


def test_queue_full_sends_busy_message():
    settings = Settings()
    app, wa, orch = build(settings, FakeOrchestrator(delay=0.3), workers=1, max_pending=2)
    with TestClient(app) as client:
        for i in range(5):
            client.post("/webhook", json=make_payload(text_msg(f"57{i}", f"id{i}", "hola")))
        assert wait_until(lambda: len(orch.seen) == 2 and len(wa.sent) >= 5, timeout=5)
    assert replies(wa).count(settings.agent.busy_message) == 3


def test_rate_limit_per_phone():
    settings = Settings()
    settings.rate_limit.max_messages = 3
    app, wa, orch = build(settings)
    with TestClient(app) as client:
        for i in range(6):
            client.post("/webhook", json=make_payload(text_msg("573009", f"r{i}", f"m{i}")))
        assert wait_until(lambda: len(orch.seen["573009"]) == 3)
        wait_until(lambda: False, timeout=0.2)
    assert orch.seen["573009"] == ["m0", "m1", "m2"]
    assert replies(wa).count(settings.agent.rate_limited_message) == 1  # notified once


def test_input_is_truncated():
    settings = Settings()
    settings.rate_limit.max_input_chars = 50
    app, wa, orch = build(settings)
    with TestClient(app) as client:
        client.post("/webhook", json=make_payload(text_msg("573010", "t1", "x" * 500)))
        assert wait_until(lambda: len(wa.sent) == 1)
    assert orch.seen["573010"] == ["x" * 50]


def test_unsupported_media_gets_notice_and_reactions_are_ignored():
    settings = Settings()
    app, wa, orch = build(settings)
    image = {"from": "573011", "id": "i1", "type": "image", "image": {"id": "123"}}
    reaction = {"from": "573011", "id": "i2", "type": "reaction", "reaction": {"emoji": "👍"}}
    with TestClient(app) as client:
        client.post("/webhook", json=make_payload(image, reaction))
        assert wait_until(lambda: len(wa.sent) >= 1)
        wait_until(lambda: False, timeout=0.2)
    assert replies(wa) == [settings.agent.unsupported_message]
    assert orch.seen == {}


def test_processing_timeout_and_circuit_open_fall_back():
    settings = Settings()
    settings.queue.processing_timeout_seconds = 0.05
    app, wa, _ = build(settings, FakeOrchestrator(delay=1.0))
    with TestClient(app) as client:
        client.post("/webhook", json=make_payload(text_msg("573012", "to1", "hola")))
        assert wait_until(lambda: len(wa.sent) == 1)
    assert replies(wa) == [settings.agent.fallback_message]

    settings = Settings()
    app, wa, _ = build(settings, FakeOrchestrator(error=CircuitOpenError("llm")))
    with TestClient(app) as client:
        client.post("/webhook", json=make_payload(text_msg("573013", "co1", "hola")))
        assert wait_until(lambda: len(wa.sent) == 1)
    assert replies(wa) == [settings.agent.fallback_message]


def test_shutdown_drains_backlog():
    app, wa, orch = build(orchestrator=FakeOrchestrator(delay=0.05), workers=1)
    with TestClient(app) as client:
        for i in range(8):
            client.post("/webhook", json=make_payload(text_msg("573014", f"d{i}", f"m{i}")))
    # leaving the context runs the lifespan shutdown -> drain
    assert orch.seen["573014"] == [f"m{i}" for i in range(8)]


def test_ready_and_metrics_endpoints():
    app, wa, _ = build()
    with TestClient(app) as client:
        client.post("/webhook", json=make_payload(text_msg("573015", "mt1", "hola")))
        assert wait_until(lambda: len(wa.sent) == 1)
        ready = client.get("/ready")
        assert ready.status_code == 200, ready.json()
        assert ready.json()["checks"]["queue"] is True
        body = client.get("/metrics").text
    assert "metabot_messages_received_total" in body
    assert "metabot_queue_pending" in body
    assert "metabot_processing_seconds_bucket" in body


def test_production_refuses_to_start_without_secrets():
    settings = Settings()
    settings.runtime.environment = "production"
    with pytest.raises(ConfigError) as exc:
        build(settings)
    message = str(exc.value)
    assert "WHATSAPP_APP_SECRET" in message


def test_production_hides_api_docs():
    settings = Settings()
    settings.runtime.environment = "production"
    settings.tree.enabled = False
    wa = settings.whatsapp
    wa.access_token = wa.phone_number_id = wa.verify_token = wa.app_secret = "x"
    settings.redirects.rules = []
    settings.redirects.default_url = "https://www.fertrac.com"
    settings.redirects.default_whatsapp = "573000000000"
    app, _, _ = build(settings)
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/health").status_code == 200
