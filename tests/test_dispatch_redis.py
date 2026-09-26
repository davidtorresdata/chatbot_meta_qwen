"""Redis queue + state against a real Redis server (skipped when unavailable)."""

import asyncio
import time
import uuid

import pytest

from src.dispatch import InboundMessage

pytestmark = pytest.mark.redis


def run(coro):
    return asyncio.run(coro)


def _client(url):
    import redis.asyncio as redis

    return redis.from_url(url, decode_responses=True)


def test_two_replicas_share_queue_with_per_phone_order(redis_url):
    from src.dispatch.redis_dispatcher import RedisDispatcher

    async def scenario():
        prefix = f"t-{uuid.uuid4().hex[:8]}"
        seen: dict[str, list[str]] = {}
        busy: set[str] = set()
        overlaps = 0
        by_replica = {"r1": 0, "r2": 0}

        def make_handler(name):
            async def handler(msg):
                nonlocal overlaps
                if msg.phone in busy:
                    overlaps += 1
                busy.add(msg.phone)
                await asyncio.sleep(0.005)
                seen.setdefault(msg.phone, []).append(msg.text)
                by_replica[name] += 1
                busy.discard(msg.phone)
            return handler

        c1, c2 = _client(redis_url), _client(redis_url)
        r1 = RedisDispatcher(c1, prefix, workers=3, max_pending=1000, poll_timeout=0.1)
        r2 = RedisDispatcher(c2, prefix, workers=3, max_pending=1000, poll_timeout=0.1)
        await r1.start(make_handler("r1"))
        await r2.start(make_handler("r2"))
        total = 0
        for i in range(10):
            for phone in ("p1", "p2", "p3", "p4", "p5"):
                target = r1 if total % 2 == 0 else r2  # ingest spread across replicas
                assert (await target.submit(InboundMessage(phone=phone, text=str(i), msg_id=f"{phone}-{i}"))).accepted
                total += 1
        deadline = time.monotonic() + 15
        while sum(len(v) for v in seen.values()) < total and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        await r1.stop()
        await r2.stop()
        pending = int(await c1.get(f"{prefix}:q:pending") or 0)
        await c1.aclose()
        await c2.aclose()
        return seen, overlaps, by_replica, pending

    seen, overlaps, by_replica, pending = run(scenario())
    assert overlaps == 0
    assert pending == 0
    assert all(texts == [str(i) for i in range(10)] for texts in seen.values())
    assert by_replica["r1"] > 0 and by_replica["r2"] > 0, "both replicas should consume"


def test_backpressure_limit_is_global(redis_url):
    from src.dispatch.redis_dispatcher import RedisDispatcher

    async def scenario():
        c = _client(redis_url)
        d = RedisDispatcher(c, f"t-{uuid.uuid4().hex[:8]}", workers=1, max_pending=2)
        d._accepting = True  # submit only; no workers started
        results = [await d.submit(InboundMessage(phone=str(i), text="x")) for i in range(3)]
        await c.aclose()
        return results

    results = run(scenario())
    assert [r.accepted for r in results] == [True, True, False]


def test_orphaned_work_of_dead_replica_is_requeued(redis_url):
    from src.dispatch.redis_dispatcher import RedisDispatcher

    async def scenario():
        prefix = f"t-{uuid.uuid4().hex[:8]}"
        c = _client(redis_url)
        # Simulate a replica that died while owning phone "x" with one queued message.
        msg = InboundMessage(phone="x", text="hola", msg_id="m1")
        await c.rpush(f"{prefix}:q:mbox:x", msg.to_json())
        await c.set(f"{prefix}:q:pending", 1)
        await c.set(f"{prefix}:q:sched:x", 1)
        await c.rpush(f"{prefix}:q:proc:dead#0", "x")
        await c.zadd(f"{prefix}:q:workers", {"dead#0": time.time() - 3600})

        got = []

        async def handler(m):
            got.append(m.text)

        d = RedisDispatcher(c, prefix, workers=1, orphan_after_seconds=30, poll_timeout=0.1)
        await d.start(handler)
        deadline = time.monotonic() + 5
        while not got and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        await d.stop()
        done_marker = await c.exists(f"{prefix}:q:done:m1")
        await c.aclose()
        return got, done_marker

    got, done_marker = run(scenario())
    assert got == ["hola"]
    assert done_marker == 1


def test_already_processed_message_is_not_replayed(redis_url):
    from src.dispatch.redis_dispatcher import RedisDispatcher

    async def scenario():
        prefix = f"t-{uuid.uuid4().hex[:8]}"
        c = _client(redis_url)
        await c.set(f"{prefix}:q:done:m1", 1)
        got = []

        async def handler(m):
            got.append(m.msg_id)

        d = RedisDispatcher(c, prefix, workers=1, poll_timeout=0.1)
        await d.start(handler)
        await d.submit(InboundMessage(phone="y", text="a", msg_id="m1"))
        await d.submit(InboundMessage(phone="y", text="b", msg_id="m2"))
        deadline = time.monotonic() + 5
        while "m2" not in got and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        await d.stop()
        await c.aclose()
        return got

    assert run(scenario()) == ["m2"]


def test_redis_state_dedup_and_rate_limit(redis_url):
    from src.state.redis_state import RedisDeduplicator, RedisRateLimiter, RedisStateStore

    async def scenario():
        prefix = f"t-{uuid.uuid4().hex[:8]}"
        c = _client(redis_url)
        store = RedisStateStore(c, prefix, 60)
        await store.set("573001", {"history": [{"role": "user", "content": "hola"}], "tree": None})
        snap = await store.get("573001")
        ttl = await c.ttl(f"{prefix}:conv:573001")
        await store.delete("573001")
        gone = await store.get("573001")

        dedup = RedisDeduplicator(c, prefix, 60)
        firsts = [await dedup.first_seen("wamid.1"), await dedup.first_seen("wamid.1")]

        limiter = RedisRateLimiter(c, prefix, 60)
        counts = [await limiter.hit("573001") for _ in range(3)]
        await c.aclose()
        return snap, ttl, gone, firsts, counts

    snap, ttl, gone, firsts, counts = run(scenario())
    assert snap["history"][0]["content"] == "hola"
    assert 0 < ttl <= 60
    assert gone is None
    assert firsts == [True, False]
    assert counts == [1, 2, 3]
