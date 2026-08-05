"""Knowledge chunking.

Pure-Python recursive text splitter (paragraph -> sentence -> word) with
configurable character overlap. No external splitter dependency needed.
"""

from __future__ import annotations

import re

_PARAGRAPH_END = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class TextChunker:
    """Splits documents into overlapping character-size chunks."""

    def __init__(self, chunk_size: int = 600, overlap: int = 100):
        if overlap >= chunk_size:
            raise ValueError("overlap must be smaller than chunk_size")
        self.chunk_size = chunk_size
        self.overlap = overlap

    def split_text(self, text: str) -> list[str]:
        text = text.replace("\r\n", "\n").strip()
        if not text:
            return []

        sentences: list[str] = []
        for paragraph in _PARAGRAPH_END.split(text):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            sentences.extend(s.strip() for s in _SENTENCE_END.split(paragraph) if s.strip())

        chunks: list[str] = []
        current: list[str] = []
        current_len = 0

        def flush() -> None:
            nonlocal current, current_len
            if current:
                chunks.append(" ".join(current))
            current = []
            current_len = 0

        for sentence in sentences:
            if len(sentence) > self.chunk_size:
                flush()
                chunks.extend(self._hard_split(sentence))
                continue

            extra = (1 if current else 0) + len(sentence)
            if current and current_len + extra > self.chunk_size:
                flush()
                # seed the next chunk with the overlap tail of the flushed chunk
                if self.overlap > 0:
                    tail = " ".join(chunks[-1].split())[-self.overlap :]
                    if tail:
                        current = [tail]
                        current_len = len(tail)

            current.append(sentence)
            current_len += len(sentence) + (1 if len(current) > 1 else 0)

        flush()
        return chunks

    def _hard_split(self, text: str) -> list[str]:
        pieces = [
            text[i : i + self.chunk_size]
            for i in range(0, len(text), self.chunk_size - self.overlap)
        ]
        return [p.strip() for p in pieces if p.strip()]
