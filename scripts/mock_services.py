"""Mock Meta Graph API + OpenAI-compatible LLM for load tests (staging / CI).

Emulates the two external dependencies so the real bot can be load-tested
end to end without WhatsApp costs or a GPU:

* ``POST /{version}/{phone_id}/messages``  Graph API send (records every reply)
* ``POST /v1/chat/completions``           LLM with latency + limited parallelism
                                           (like Ollama's OLLAMA_NUM_PARALLEL)
* ``POST /v1/embeddings``                 deterministic hashed bag-of-words vectors
* ``GET  /v1/models``                     readiness probe
* ``GET  /stats`` / ``POST /reset``       counters for the load test

Run:
    python scripts/mock_services.py --port 9100 --llm-latency 1.5 --llm-parallel 2
Point the bot at it:
    WHATSAPP_GRAPH_BASE_URL=http://localhost:9100
    LLM_BASE_URL=http://localhost:9100/v1  EMBEDDING_BASE_URL=http://localhost:9100/v1
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import math
import re
import time
from collections import defaultdict

from fastapi import FastAPI, Request

DIM = 64


def embed(text: str) -> list[float]:
    vec = [0.0] * DIM
    for word in re.findall(r"\w+", text.lower()):
        vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def build_app(llm_latency: float, llm_parallel: int) -> FastAPI:
    app = FastAPI(title="mock-meta-llm")
    gate = asyncio.Semaphore(llm_parallel)
    stats = {"llm_calls": 0, "llm_active": 0, "llm_max_active": 0, "sent": 0}
    sent_by_phone: dict[str, list[str]] = defaultdict(list)

    @app.post("/{version}/{phone_id}/messages")
    async def graph_send(request: Request):
        body = await request.json()
        if body.get("status") == "read":
            return {"success": True}
        text = (body.get("text") or {}).get("body") or ((body.get("interactive") or {}).get("body") or {}).get("text", "")
        sent_by_phone[body.get("to", "?")].append(text)
        stats["sent"] += 1
        return {"messaging_product": "whatsapp", "messages": [{"id": f"wamid.mock{stats['sent']}"}]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        system = body["messages"][0]["content"]
        match = re.search(r"\[1\] (.+?)(?:\n---|\n=== END)", system, re.S)
        answer = (match.group(1) if match else "Sin contexto").strip()[:240]
        async with gate:  # Ollama serves at most N requests in parallel
            stats["llm_active"] += 1
            stats["llm_max_active"] = max(stats["llm_max_active"], stats["llm_active"])
            try:
                await asyncio.sleep(llm_latency)
            finally:
                stats["llm_active"] -= 1
        stats["llm_calls"] += 1
        return {
            "id": "mock", "object": "chat.completion", "created": int(time.time()), "model": body.get("model"),
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": answer}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    @app.post("/v1/embeddings")
    async def embeddings(request: Request):
        body = await request.json()
        inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
        return {"object": "list", "model": body.get("model"),
                "data": [{"object": "embedding", "index": i, "embedding": embed(t)} for i, t in enumerate(inputs)],
                "usage": {"prompt_tokens": 0, "total_tokens": 0}}

    @app.get("/v1/models")
    async def models():
        return {"object": "list", "data": [{"id": "mock", "object": "model", "owned_by": "mock"}]}

    @app.get("/stats")
    async def get_stats():
        return {**stats, "phones": len(sent_by_phone), "sent_by_phone": sent_by_phone}

    @app.post("/reset")
    async def reset():
        stats.update(llm_calls=0, llm_active=0, llm_max_active=0, sent=0)
        sent_by_phone.clear()
        return {"ok": True}

    return app


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--llm-latency", type=float, default=1.5, help="seconds per completion")
    parser.add_argument("--llm-parallel", type=int, default=2, help="like OLLAMA_NUM_PARALLEL")
    args = parser.parse_args()
    uvicorn.run(build_app(args.llm_latency, args.llm_parallel), host=args.host, port=args.port, log_level="warning")
