"""Interactive chat simulator for the WhatsApp chatbot.

Feeds messages through the real orchestrator (tree + guardrails + RAG + Qwen)
and prints exactly what the bot would reply, WITHOUT needing a WhatsApp app,
a Meta account or a network connection to Facebook.

Usage (interactive):
    docker compose exec chatbot python scripts/chat_cli.py
    python scripts/chat_cli.py            # local, if you have deps installed

Usage (one-shot / scripted):
    echo "menu" | docker compose exec chatbot python scripts/chat_cli.py
    python scripts/chat_cli.py --phone 15550000000 < input.txt

Commands:
    /reset   start a clean conversation for the phone
    /quit    exit
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent.orchestrator import ReplyAction, WhatsAppOrchestrator
from src.config import load_settings
from src.knowledge.embedding import build_embedder
from src.knowledge.vector_store import VectorStore
from src.llm.qwen import QwenClient
from src.utils.logging import setup_logging


def render(action: ReplyAction) -> str:
    lines = [f"[{action.type}]"]
    if action.message:
        lines.append(action.message)
    if action.url:
        lines.append(f"url: {action.url}")
    if action.whatsapp:
        lines.append(f"whatsapp: {action.whatsapp}")
    return "\n".join(lines)


async def run(phone: str) -> int:
    setup_logging()
    settings = load_settings()
    store = VectorStore(settings.storage.lancedb_path, settings.storage.table_name)
    embedder = build_embedder(settings.embeddings, llm_base_url=settings.llm.base_url)
    qwen = QwenClient(settings.llm)
    orch = WhatsAppOrchestrator(settings, store, embedder, qwen)

    print(f"Bot: {settings.agent.welcome_message or 'Connected. Type a message.'}")
    print(f"Bot: (you are simulating phone {phone}; /reset starts over, /quit exits)")

    if sys.stdin.isatty():
        print("-" * 60)
        while True:
            try:
                line = input("You: ").strip()
            except EOFError:
                break
            if not line:
                continue
            if line == "/quit":
                break
            if line == "/reset":
                await orch.reset_conversation(phone)
                print("Bot: conversation reset")
                continue
            action = await orch.handle_message(phone, line)
            print(f"Bot:\n{render(action)}")
            print("-" * 60)
    else:
        for line in sys.stdin:
            line = line.strip()
            if not line or line == "/quit":
                continue
            if line == "/reset":
                await orch.reset_conversation(phone)
                print("Bot: conversation reset")
                continue
            action = await orch.handle_message(phone, line)
            print(f"You: {line}")
            print(f"Bot:\n{render(action)}")
            print("-" * 60)

    await qwen.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phone", default="15550000000", help="fake phone number to simulate")
    args = parser.parse_args()
    return asyncio.run(run(args.phone))


if __name__ == "__main__":
    sys.exit(main())
