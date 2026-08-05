"""Smoke test for the ingestion pipeline (chunk -> embed -> store)."""

import pytest

from src.knowledge.chunker import TextChunker
from src.knowledge.ingest import chunk_documents
from src.knowledge.vector_store import VectorStore


class FakeEmbedder:
    async def embed(self, texts):
        return [[0.1 * i, 0.2, 0.3] for i in range(len(texts))]

    @property
    def dimension(self):
        return 3


@pytest.fixture()
def markdown_file(tmp_path):
    path = tmp_path / "doc.md"
    path.write_text(
        "# Title\n\n"
        "First paragraph about products. Second sentence here.\n\n"
        "Second paragraph about support and returns with a bit more detail."
    , encoding="utf-8")
    return path


def test_chunk_and_ingest(tmp_path, markdown_file):
    chunker = TextChunker(chunk_size=80, overlap=15)
    documents = chunk_documents([markdown_file], chunker)
    assert documents

    store = VectorStore(str(tmp_path / "db"), "kb")
    store.reset()
    from src.knowledge.ingest import ingest_documents
    total = pytest_await(ingest_documents(store, FakeEmbedder(), documents))
    assert total == len(documents)
    assert store.count() == len(documents)

    first = store.search([0.0, 0.2, 0.3], top_k=1)[0]
    assert first.metadata["source"].startswith(str(markdown_file))
    store.reset()


def test_collect_files_only_supported(tmp_path):
    from src.knowledge.ingest import collect_files

    (tmp_path / "a.md").write_text("x")
    (tmp_path / "b.pdf").write_text("x")
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "prices.xlsx").write_bytes(b"x")
    (tmp_path / "image.png").write_bytes(b"png")
    files = collect_files(tmp_path)
    names = {f.suffix for f in files}
    assert names == {".md", ".pdf", ".txt", ".xlsx"}


def test_excel_rows_become_blocks(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    from src.knowledge.ingest import load_excel_rows

    path = tmp_path / "prices.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Pricing"
    ws.append(["Product", "Price", "Warranty"])
    ws.append(["Premium Widget Pro", 129.99, "2 years"])
    ws.append([])
    ws.append(["Standard Widget", 49.99, "1 year"])
    wb.save(path)

    blocks = load_excel_rows(path)
    assert len(blocks) == 2
    assert "[sheet 'Pricing', row 2]" in blocks[0]
    assert "Product: Premium Widget Pro" in blocks[0]
    assert "Price: 129.99" in blocks[0]
    assert "Warranty: 2 years" in blocks[0]
    assert "Standard Widget" in blocks[1]


def test_excel_through_chunk_documents(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    from src.knowledge.chunker import TextChunker
    from src.knowledge.ingest import chunk_documents

    path = tmp_path / "data.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Question", "Answer"])
    ws.append(["What is the return policy?", "30 days with original packaging."])
    ws.append(["Where is support?", "support@acme.example.com"])
    wb.save(path)

    documents = chunk_documents([path], TextChunker(chunk_size=200, overlap=20))
    assert documents
    joined = " ".join(d.text for d in documents)
    assert "return policy" in joined
    assert "support@acme.example.com" in joined


def test_reopen_existing_table(tmp_path):
    from src.knowledge.vector_store import VectorStore

    path = str(tmp_path / "db")
    v1 = VectorStore(path, "kb")
    v1.add([[0.1, 0.2, 0.3]], ["hello world"], [{"source": "test"}])

    v2 = VectorStore(path, "kb")  # fresh connection, table already exists
    assert v2.count() == 1
    hits = v2.search([0.1, 0.2, 0.3], top_k=1)
    assert hits and "hello world" in hits[0].text
    v2.add([[0.4, 0.5, 0.6]], ["second row"], [{"source": "test"}])
    assert v2.count() == 2


def pytest_await(coro):
    import asyncio

    return asyncio.run(coro)
