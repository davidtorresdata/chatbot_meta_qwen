"""In-process queue: per-phone order, bounded parallelism, backpressure, drain."""

import asyncio

from src.dispatch import InboundMessage, LocalDispatcher


def run(coro):
    return asyncio.run(coro)


def test_per_phone_order_and_bounded_parallelism():
    async def scenario():
        active = max_active = 0
        busy: set[str] = set()
        overlaps = 0
        seen: dict[str, list[str]] = {}

        async def handler(msg: InboundMessage):
            nonlocal active, max_active, overlaps
            if msg.phone in busy:
                overlaps += 1
            busy.add(msg.phone)
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            seen.setdefault(msg.phone, []).append(msg.text)
            active -= 1
            busy.discard(msg.phone)

        d = LocalDispatcher(workers=4, max_pending=1000, drain_timeout=10)
        await d.start(handler)
        for i in range(8):
            for phone in ("A", "B", "C", "D", "E", "F"):
                assert (await d.submit(InboundMessage(phone=phone, text=str(i)))).accepted
        await d.stop()
        return seen, max_active, overlaps, d.pending

    seen, max_active, overlaps, pending = run(scenario())
    assert pending == 0
    assert overlaps == 0, "the same conversation was processed concurrently"
    assert 1 < max_active <= 4
    for texts in seen.values():
        assert texts == [str(i) for i in range(8)]


def test_fairness_chatty_phone_does_not_starve_others():
    async def scenario():
        order: list[str] = []

        async def handler(msg):
            order.append(msg.phone)
            await asyncio.sleep(0)

        d = LocalDispatcher(workers=1, max_pending=100)
        await d.start(handler)
        for _ in range(5):
            await d.submit(InboundMessage(phone="chatty", text="x"))
        await d.submit(InboundMessage(phone="quiet", text="y"))
        await d.stop()
        return order

    order = run(scenario())
    assert order.index("quiet") <= 2  # served after at most 2 of the chatty messages


def test_backpressure_rejects_when_full():
    async def scenario():
        gate = asyncio.Event()

        async def handler(msg):
            await gate.wait()

        d = LocalDispatcher(workers=1, max_pending=3, drain_timeout=5)
        await d.start(handler)
        results = [await d.submit(InboundMessage(phone=str(i), text="x")) for i in range(5)]
        gate.set()
        await d.stop()
        return results

    results = run(scenario())
    assert [r.accepted for r in results] == [True, True, True, False, False]
    assert results[-1].reason == "full"


def test_handler_error_does_not_kill_worker():
    async def scenario():
        done = []

        async def handler(msg):
            if msg.text == "boom":
                raise RuntimeError("boom")
            done.append(msg.text)

        d = LocalDispatcher(workers=1, max_pending=10)
        await d.start(handler)
        await d.submit(InboundMessage(phone="1", text="boom"))
        await d.submit(InboundMessage(phone="1", text="ok"))
        await d.stop()
        return done

    assert run(scenario()) == ["ok"]


def test_stop_drains_and_then_rejects():
    async def scenario():
        done = []

        async def handler(msg):
            await asyncio.sleep(0.02)
            done.append(msg.text)

        d = LocalDispatcher(workers=2, max_pending=100, drain_timeout=5)
        await d.start(handler)
        for i in range(10):
            await d.submit(InboundMessage(phone=str(i % 3), text=str(i)))
        await d.stop()
        late = await d.submit(InboundMessage(phone="9", text="late"))
        return done, late

    done, late = run(scenario())
    assert len(done) == 10
    assert not late.accepted and late.reason == "stopping"
