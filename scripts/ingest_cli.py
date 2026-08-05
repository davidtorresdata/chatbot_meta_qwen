"""CLI to ingest knowledge documents into the local LanceDB base.

Usage:
    python scripts/ingest_cli.py knowledge_base/
    python scripts/ingest_cli.py docs/faq.md --reset
    python scripts/ingest_cli.py knowledge_base/ --chunk-size 500 --overlap 80
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_settings
from src.knowledge.chunker import TextChunker
from src.knowledge.embedding import build_embedder
from src.knowledge.ingest import chunk_documents, collect_files, ingest_documents
from src.knowledge.vector_store import VectorStore
from src.utils.logging import setup_logging


async def _run(args: argparse.Namespace) -> int:
    settings = load_settings()

    source = Path(args.source)
    files = collect_files(source)
    if not files:
        print(f"No supported documents found in {source}")
        return 1

    chunker = TextChunker(chunk_size=args.chunk_size or settings.storage.chunk_size,
                          overlap=args.overlap or settings.storage.chunk_overlap)
    documents = chunk_documents(files, chunker)
    if not documents:
        print("No text extracted from the documents.")
        return 1

    store = VectorStore(settings.storage.lancedb_path, settings.storage.table_name)
    if args.reset:
        store.reset()
        print("Existing table dropped.")

    embedder = build_embedder(settings.embeddings, llm_base_url=settings.llm.base_url)
    total = await ingest_documents(store, embedder, documents)
    print(f"Ingested {total} chunks from {len(files)} files into "
          f"'{settings.storage.table_name}' (LanceDB at {settings.storage.lancedb_path}).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest knowledge into LanceDB")
    parser.add_argument("source", help="a file or directory of .txt/.md/.pdf documents")
    parser.add_argument("--reset", action="store_true", help="drop the existing table first")
    parser.add_argument("--chunk-size", type=int, default=None)
    parser.add_argument("--overlap", type=int, default=None)
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Ingest action | source=%s reset=%s", args.source, args.reset)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
