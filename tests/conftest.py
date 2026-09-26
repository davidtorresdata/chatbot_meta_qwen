"""Shared fixtures: fakes for the app components and an ephemeral Redis."""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import time
from collections import defaultdict

import pytest

from src.agent.orchestrator import ReplyAction


# ---------------------------------------------------------------- fakes
class FakeWhatsApp:
    signature_enabled = False
    configured = True

    def __init__(self):
        self.sent: list[tuple[str, str]] = []
        self.read: list[str] = []

    def verify_webhook(self, mode, token, challenge):
        return challenge if mode == "subscribe" and token == "t" else None

    def verify_signature(self, raw, header):
        return True

    async def send_text(self, to, body):
        self.sent.append((to, body))

    async def send_cta_url(self, to, body, url, button_text="Open"):
        self.sent.append((to, body))

    async def mark_read(self, message_id):
        self.read.append(message_id)

    async def close(self):
        pass


class FakeOrchestrator:
    """Records concurrency and per-phone order; fails if one phone overlaps."""

    def __init__(self, delay: float = 0.02, error: Exception | None = None):
        self.delay = delay
        self.error = error
        self.active = 0
        self.max_active = 0
        self.busy: set[str] = set()
        self.overlaps = 0
        self.seen: dict[str, list[str]] = defaultdict(list)

    async def handle_message(self, phone, text):
        if phone in self.busy:
            self.overlaps += 1
        self.busy.add(phone)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            if self.error:
                raise self.error
            self.seen[phone].append(text)
            return ReplyAction(type="text", message=f"eco:{text}")
        finally:
            self.active -= 1
            self.busy.discard(phone)


class FakeVectorStore:
    def count(self):
        return 3


class FakeQwen:
    breaker = None

    async def ping(self):
        return True

    async def close(self):
        pass


def make_payload(*messages: dict) -> dict:
    return {"object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {"messages": list(messages)}}]}]}


def text_msg(phone: str, msg_id: str, body: str) -> dict:
    return {"from": phone, "id": msg_id, "type": "text", "text": {"body": body}}


def wait_until(predicate, timeout: float = 10.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ---------------------------------------------------------------- redis
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def redis_url(tmp_path_factory):
    """REDIS_URL from the environment (CI service) or a throwaway redis-server."""
    pytest.importorskip("redis")
    if os.getenv("REDIS_URL"):
        yield os.environ["REDIS_URL"]
        return
    binary = shutil.which("redis-server")
    if not binary:
        pytest.skip("no REDIS_URL and no redis-server binary")
    port = _free_port()
    workdir = tmp_path_factory.mktemp("redis")
    proc = subprocess.Popen(
        [binary, "--port", str(port), "--save", "", "--appendonly", "no", "--dir", str(workdir)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.05)
        yield f"redis://127.0.0.1:{port}/0"
    finally:
        proc.terminate()
        proc.wait(timeout=5)
