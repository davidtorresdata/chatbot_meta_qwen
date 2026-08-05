"""Local LanceDB vector store.

Embedded LanceDB keeps the knowledge base on local disk (path from
config ``storage.lancedb_path``), so it scales locally without any
external service. Vector search uses cosine distance.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import lancedb


@dataclass
class SearchHit:
    text: str
    similarity: float  # 0..1, higher is more relevant
    metadata: dict


class VectorStore:
    def __init__(self, lancedb_path: str | Path, table_name: str = "knowledge_base"):
        self._path = str(lancedb_path)
        self._table_name = table_name
        self._db = lancedb.connect(self._path)
        self._table = None

    def _table_exists(self) -> bool:
        try:
            tables = self._db.list_tables()
        except AttributeError:  # legacy lancedb API
            tables = self._db.table_names()
        if isinstance(tables, (list, tuple, set)):
            names = list(tables)
        elif hasattr(tables, "tables"):  # new API returns a TableNames object
            names = list(tables.tables)
        else:
            names = [str(t) for t in tables]
        return self._table_name in names

    def _ensure_table(self, dimension: int) -> None:
        if self._table is not None:
            return
        if self._table_exists():
            self._table = self._db.open_table(self._table_name)
        else:
            self._table = self._db.create_table(
                self._table_name,
                data=[{"vector": [0.0] * dimension, "text": "", "metadata": "{}"}],
            )
            self._table.delete("text = ''")

    def add(self, vectors: list[list[float]], texts: list[str], metadatas: list[dict]) -> int:
        if not texts:
            return 0
        dimension = len(vectors[0])
        self._ensure_table(dimension)
        rows = [
            {"vector": vec, "text": text, "metadata": json.dumps(meta or {})}
            for vec, text, meta in zip(vectors, texts, metadatas)
        ]
        self._table.add(rows)
        return len(rows)

    def _search(self, query_vector: list[float], top_k: int) -> list[dict]:
        self._ensure_table(len(query_vector))
        if hasattr(self._table, "vector_search"):
            results = self._table.vector_search(query_vector).limit(top_k).to_list()
        else:  # older lancedb API
            results = self._table.search(query_vector).limit(top_k).to_list()
        return results

    def search(self, query_vector: list[float], top_k: int = 5) -> list[SearchHit]:
        results = self._search(query_vector, top_k)
        hits: list[SearchHit] = []
        for row in results:
            distance = float(row.get("_distance", row.get("distance", 1.0)))
            # cosine distance in [0, 2] -> similarity in [0, 1]
            similarity = max(0.0, min(1.0, 1.0 - distance))
            try:
                metadata = json.loads(row.get("metadata", "{}")) if isinstance(row.get("metadata"), str) else {}
            except (json.JSONDecodeError, TypeError):
                metadata = {}
            hits.append(
                SearchHit(
                    text=str(row.get("text", "")),
                    similarity=similarity,
                    metadata=metadata,
                )
            )
        return hits

    async def search_async(self, query_vector: list[float], top_k: int = 5) -> list[SearchHit]:
        return await asyncio.to_thread(self.search, query_vector, top_k)

    def count(self) -> int:
        if self._table is None:
            if not self._table_exists():
                return 0
            self._table = self._db.open_table(self._table_name)
        return self._table.count_rows()

    def reset(self) -> None:
        if self._table_exists():
            self._db.drop_table(self._table_name)
        self._table = None
