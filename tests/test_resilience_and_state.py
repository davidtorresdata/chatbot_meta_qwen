"""Circuit breaker, Meta retries, memory state, PII masking, config validation, registry batching."""

import asyncio
import sqlite3

import httpx
import pytest

from src.config import PROJECT_ROOT, Settings, load_settings, validate_settings
from src.state.memory import MemoryDeduplicator, MemoryRateLimiter, MemoryStateStore, TTLCache
from src.utils.pii import content_for_log, mask_phone
from src.utils.resilience import CircuitBreaker, CircuitOpenError


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ---------------------------------------------------------------- breaker
def test_circuit_breaker_opens_half_opens_and_closes():
    clock = Clock()
    cb = CircuitBreaker("llm", failure_threshold=3, cooldown_seconds=10, clock=clock)
    for _ in range(3):
        cb.before_call()
        cb.record_failure()
    assert cb.state == "open"
    with pytest.raises(CircuitOpenError):
        cb.before_call()
    clock.t += 10
    assert cb.state == "half_open"
    cb.before_call()  # one trial allowed
    with pytest.raises(CircuitOpenError):
        cb.before_call()  # second concurrent trial refused
    cb.record_success()
    assert cb.state == "closed"


def test_circuit_breaker_trial_failure_reopens():
    clock = Clock()
    cb = CircuitBreaker("llm", failure_threshold=1, cooldown_seconds=5, clock=clock)
    cb.record_failure()
    clock.t += 5
    cb.before_call()
    cb.record_failure()
    assert cb.state == "open"


# ---------------------------------------------------------------- meta retries
def _meta_client(monkeypatch, responses):
    from src.config import WhatsAppConfig
    from src.whatsapp import meta

    monkeypatch.setattr(meta, "backoff_delay", lambda attempt: 0)
    calls = []

    def handler(request):
        calls.append(request)
        status = responses[min(len(calls), len(responses)) - 1]
        return httpx.Response(status, json={"ok": status < 400})

    client = meta.MetaWhatsAppClient(WhatsAppConfig(access_token="t", phone_number_id="1", max_retries=3))
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client, calls, meta


def test_meta_retries_5xx_and_429_then_succeeds(monkeypatch):
    client, calls, _ = _meta_client(monkeypatch, [500, 429, 200])
    asyncio.run(client.send_text("573001", "hola"))
    assert len(calls) == 3


def test_meta_does_not_retry_client_errors(monkeypatch):
    client, calls, meta = _meta_client(monkeypatch, [400])
    with pytest.raises(meta.MetaWhatsAppError):
        asyncio.run(client.send_text("573001", "hola"))
    assert len(calls) == 1


# ---------------------------------------------------------------- memory state
def test_ttl_cache_expires_and_evicts_lru():
    clock = Clock()
    cache = TTLCache(ttl_seconds=10, max_items=2, clock=clock)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")  # a is now most recent
    cache.set("c", 3)  # evicts b
    assert cache.get("b") is None and cache.get("a") == 1
    clock.t += 11
    assert cache.get("a") is None


def test_memory_state_dedup_and_rate_limit():
    async def scenario():
        clock = Clock()
        store = MemoryStateStore(ttl_seconds=60, max_items=10, clock=clock)
        await store.set("1", {"history": [1]})
        snap = await store.get("1")
        snap["history"].append(2)  # returned copies must not alias stored state
        again = await store.get("1")
        clock.t += 61
        expired = await store.get("1")

        dedup = MemoryDeduplicator(60, clock=clock)
        firsts = [await dedup.first_seen("m"), await dedup.first_seen("m")]

        limiter = MemoryRateLimiter(window_seconds=60, clock=clock)
        counts = [await limiter.hit("p") for _ in range(3)]
        clock.t += 60
        next_window = await limiter.hit("p")
        return again, expired, firsts, counts, next_window

    again, expired, firsts, counts, next_window = asyncio.run(scenario())
    assert again == {"history": [1]}
    assert expired is None
    assert firsts == [True, False]
    assert counts == [1, 2, 3] and next_window == 1


def test_orchestrator_state_survives_via_store_and_expires(tmp_path):
    """Tree session lives in the state store between messages, not in the process."""
    from src.agent.orchestrator import WhatsAppOrchestrator

    tree = tmp_path / "tree.md"
    tree.write_text("## f\nKeywords: pedido\n- question: Numero? -> field=o\n- answer: Orden {o} ok\n", encoding="utf-8")
    settings = load_settings()
    settings.tree.enabled = True
    settings.tree.path = str(tree)
    clock = Clock()
    store = MemoryStateStore(ttl_seconds=60, max_items=100, clock=clock)
    orch = WhatsAppOrchestrator(settings, None, None, None, state_store=store)

    async def scenario():
        first = await orch.handle_message("573020", "mi pedido")
        assert orch._tree is not None and not orch._tree.active("573020")  # nothing kept in-process
        snapshot = await store.get("573020")
        second = await orch.handle_message("573020", "A-1")
        return first, snapshot, second, await store.get("573020")

    first, snapshot, second, after = asyncio.run(scenario())
    assert "Numero?" in first.message
    assert snapshot["tree"]["flow"] == "f"
    assert "Orden A-1 ok" in second.message
    assert after is None  # finished flow + no chat history -> state cleared


# ---------------------------------------------------------------- pii / logs
def test_mask_phone_and_content_logging(monkeypatch):
    assert mask_phone("573001234567") == "5730*****567"
    assert mask_phone(None) == "-"
    monkeypatch.delenv("LOG_MESSAGE_CONTENT", raising=False)
    assert content_for_log("mi cedula es 123") == "<16 chars>"
    monkeypatch.setenv("LOG_MESSAGE_CONTENT", "1")
    assert "cedula" in content_for_log("mi cedula es 123")


# ---------------------------------------------------------------- config
def test_validation_flags_placeholders_and_missing_secrets():
    problems = validate_settings(load_settings(PROJECT_ROOT / "config" / "config.yaml"))
    joined = "\n".join(problems)
    assert "WHATSAPP_APP_SECRET is not set" in joined
    assert "example.com" in joined or "15551234567" in joined


def test_validation_requires_redis_url_for_redis_backends():
    settings = Settings()
    settings.queue.backend = "redis"
    assert any("REDIS_URL" in p for p in validate_settings(settings))


def test_profile_overlay_and_env_overrides(tmp_path, monkeypatch):
    base = tmp_path / "config.yaml"
    base.write_text("app:\n  name: Base\nqueue:\n  workers: 2\n", encoding="utf-8")
    (tmp_path / "config.production.yaml").write_text("app:\n  name: Prod\n", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("QUEUE_WORKERS", "6")
    settings = load_settings(base)
    assert settings.app.name == "Prod"
    assert settings.queue.workers == 6
    assert settings.is_production


# ---------------------------------------------------------------- registry
def test_registry_background_writer_batches_everything(tmp_path):
    from src.config import ConversationLogConfig
    from src.registry.backends import SqliteBackend
    from src.registry.service import ConversationRegistry

    db = tmp_path / "log.sqlite3"
    config = ConversationLogConfig(enabled=True, backend="sqlite", db_path=str(db))
    backend = SqliteBackend(db, config.columns)
    batches = []
    original = backend.log_many
    backend.log_many = lambda records: (batches.append(len(records)), original(records))

    async def scenario():
        registry = ConversationRegistry(config, backend, batch_size=50, flush_interval=0.05)
        registry.start()
        await asyncio.gather(*(registry.log_turn(str(i), "p", "r") for i in range(120)))
        await registry.aclose()

    asyncio.run(scenario())
    rows = sqlite3.connect(db).execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    assert rows == 120
    assert max(batches) > 1 and len(batches) < 120
