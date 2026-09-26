"""Webhook load generator: N concurrent customers x M messages each.

Sends Meta-shaped, correctly signed (X-Hub-Signature-256) webhooks as fast as
allowed and reports ingestion latency. With ``--mock`` (scripts/mock_services.py)
it also waits until every message was processed and every customer answered.
(Per-conversation ordering is covered by tests/test_app_pipeline.py.)

Examples:
    # staging, against the real bot + mock Meta/LLM
    python scripts/load_test.py --url http://localhost:8000 --secret $WHATSAPP_APP_SECRET \\
        --phones 50 --messages 4 --mock http://localhost:9100

    # only ingestion (no reply tracking)
    python scripts/load_test.py --url https://bot.fertrac.com --secret ... --phones 20 --messages 2

Do NOT point it at production with real customer numbers: replies are sent via Meta.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import statistics
import sys
import time
import uuid

import httpx

# Default questions target the sample knowledge base (RAG + LLM path). Use
# --questions with one question per line to match your real knowledge base.
QUESTIONS = [
    "What colors is the Standard Widget available in?",
    "What does the warranty cover?",
    "Which widget includes an extended two-year warranty?",
    "Are invoices available in the customer portal?",
]


def payload(phone: str, msg_id: str, text: str) -> bytes:
    body = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"messages": [
        {"from": phone, "id": msg_id, "timestamp": str(int(time.time())), "type": "text", "text": {"body": text}}
    ]}}]}]}
    return json.dumps(body, ensure_ascii=False).encode()


def sign(raw: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


async def customer(client, url, secret, phone, messages, gap, latencies, errors, questions):
    for n in range(messages):
        raw = payload(phone, f"wamid.load.{uuid.uuid4().hex}", questions[(n + int(phone[-2:])) % len(questions)])
        headers = {"Content-Type": "application/json"}
        if secret:
            headers["X-Hub-Signature-256"] = sign(raw, secret)
        started = time.perf_counter()
        try:
            response = await client.post(f"{url}/webhook", content=raw, headers=headers)
            if response.status_code != 200:
                errors.append(response.status_code)
        except httpx.HTTPError as exc:
            errors.append(type(exc).__name__)
        latencies.append(time.perf_counter() - started)
        if gap:
            await asyncio.sleep(gap)


async def processed_total(client, url) -> float | None:
    """Sum of metabot_messages_processed_total (None if /metrics is not reachable)."""
    try:
        text = (await client.get(f"{url}/metrics")).text
    except httpx.HTTPError:
        return None
    return sum(float(line.rsplit(" ", 1)[1]) for line in text.splitlines()
               if line.startswith("metabot_messages_processed_total{"))


def pct(values, q):
    return statistics.quantiles(values, n=100)[q - 1] if len(values) >= 2 else (values[0] if values else 0)


async def main(args) -> int:
    phones = [f"57399{i:07d}" for i in range(args.phones)]
    questions = QUESTIONS
    if args.questions:
        with open(args.questions, encoding="utf-8") as fh:
            questions = [line.strip() for line in fh if line.strip()] or QUESTIONS
    latencies: list[float] = []
    errors: list = []
    async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=200)) as client:
        url = args.url.rstrip("/")
        if args.mock:
            await client.post(f"{args.mock}/reset")
        baseline = await processed_total(client, url)
        started = time.perf_counter()
        await asyncio.gather(*(
            customer(client, url, args.secret, p, args.messages, args.gap, latencies, errors, questions)
            for p in phones
        ))
        ingest_time = time.perf_counter() - started
        total = args.phones * args.messages
        print(f"Ingestion: {total} webhooks in {ingest_time:.2f}s ({total / ingest_time:.0f} req/s)")
        print(f"  webhook latency p50={pct(latencies, 50) * 1000:.0f}ms p95={pct(latencies, 95) * 1000:.0f}ms "
              f"max={max(latencies) * 1000:.0f}ms errors={len(errors)}")
        if not args.mock:
            return 1 if errors else 0

        # Done when the bot reports every message processed (/metrics), or - if
        # /metrics is not reachable - when every customer got >= M sends.
        deadline = time.monotonic() + args.timeout
        stats = {}
        while time.monotonic() < deadline:
            stats = (await client.get(f"{args.mock}/stats")).json()
            now = await processed_total(client, url)
            if baseline is not None and now is not None:
                if now - baseline >= total:
                    break
            elif all(len(stats["sent_by_phone"].get(p, [])) >= args.messages for p in phones):
                break
            await asyncio.sleep(0.5)
        elapsed = time.perf_counter() - started
        replies = {p: stats["sent_by_phone"].get(p, []) for p in phones}
        got = sum(len(v) for v in replies.values())
        print(f"Replies: {got} Meta sends for {total} messages in {elapsed:.1f}s "
              f"(LLM calls={stats.get('llm_calls')}, LLM max parallel={stats.get('llm_max_active')})")
        if args.metrics:
            try:
                text = (await client.get(f"{url}/metrics")).text
            except httpx.HTTPError as exc:
                text = ""
                print(f"  /metrics not reachable: {type(exc).__name__}")
            for line in text.splitlines():
                if line.startswith(("metabot_messages_processed_total", "metabot_messages_rejected_total",
                                    "metabot_messages_duplicate_total")):
                    print("  " + line)
        return 1 if errors or got < total else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--secret", default="", help="WHATSAPP_APP_SECRET of the target")
    parser.add_argument("--phones", type=int, default=20)
    parser.add_argument("--messages", type=int, default=3)
    parser.add_argument("--gap", type=float, default=0.0, help="seconds between a customer's messages")
    parser.add_argument("--mock", default="", help="mock_services.py base URL to verify replies")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--questions", default="", help="file with one question per line")
    parser.add_argument("--metrics", action="store_true", help="print bot counters from /metrics")
    sys.exit(asyncio.run(main(parser.parse_args())))
