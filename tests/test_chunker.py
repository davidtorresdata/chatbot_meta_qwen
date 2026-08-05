"""Tests for the text chunker."""

from src.knowledge.chunker import TextChunker


def test_simple_split_respects_chunk_size():
    chunker = TextChunker(chunk_size=50, overlap=10)
    text = " ".join(["sentence number {}".format(i) for i in range(40)])
    chunks = chunker.split_text(text)
    assert chunks
    assert all(len(c) <= 50 for c in chunks)


def test_empty_text():
    assert TextChunker().split_text("   ") == []
    assert TextChunker().split_text("") == []


def test_overlap_preserved():
    chunker = TextChunker(chunk_size=100, overlap=25)
    text = ("This is the first sentence of the document. "
            "Here comes a second sentence with more words. "
            "A third sentence keeps the story going longer. "
            "Finally a fourth sentence wraps everything up nicely.")
    chunks = chunker.split_text(text)
    assert len(chunks) >= 2
    assert chunks[1].startswith(chunks[0][-25:].split()[0])


def test_overlap_must_be_smaller_than_chunk():
    import pytest

    with pytest.raises(ValueError):
        TextChunker(chunk_size=50, overlap=60)


def test_single_long_sentence_is_hard_split():
    chunker = TextChunker(chunk_size=40, overlap=5)
    text = "word " * 30
    chunks = chunker.split_text(text)
    assert len(chunks) >= 2
    assert all(len(c) <= 40 for c in chunks)
