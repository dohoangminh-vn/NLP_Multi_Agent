"""Persistent BM25 repository with lesson-scope filtering."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from rank_bm25 import BM25L

from src.rag.exceptions import IndexBuildError
from src.rag.interfaces import VietnameseTokenizer
from src.rag.models import DocumentChunk


class PersistentBM25Repository:
    """Build, save and search a BM25 corpus through a tokenizer strategy."""

    _filename = "index.json"
    _format_version = 1

    def __init__(self, tokenizer: VietnameseTokenizer) -> None:
        self._tokenizer = tokenizer
        self._chunks: list[DocumentChunk] = []
        self._tokens: list[list[str]] = []
        self._index: BM25L | None = None

    def build_index(self, chunks: list[DocumentChunk]) -> None:
        """Replace the current corpus with validated chunks and their token lists."""
        ids = [chunk.chunk_id for chunk in chunks]
        if len(ids) != len(set(ids)):
            raise IndexBuildError("Duplicate chunk_id in BM25 corpus")
        tokens = [self._tokenizer.tokenize(chunk.content) for chunk in chunks]
        self._set_corpus(chunks, tokens)

    def search(
        self, query: str, top_k: int = 5, target_bai: int | None = None
    ) -> list[tuple[DocumentChunk, float]]:
        """Return scored hits among lessons available at the target lesson."""
        if top_k < 1:
            raise ValueError("top_k must be positive")
        if target_bai is not None and not 16 <= target_bai <= 29:
            raise ValueError("target_bai must be between 16 and 29")
        query_tokens = self._tokenizer.tokenize(query)
        if not query_tokens or self._index is None:
            return []
        allowed = [
            index for index, chunk in enumerate(self._chunks)
            if target_bai is None or chunk.metadata.bai_so <= target_bai
        ]
        scores = self._index.get_scores(query_tokens)
        ranked = sorted(
            ((self._chunks[index], float(scores[index])) for index in allowed if scores[index] > 0),
            key=lambda hit: (-hit[1], hit[0].chunk_id),
        )[:top_k]
        for chunk, _ in ranked:
            if target_bai is not None:
                chunk.validate_scope(target_bai)
        return ranked

    def save(self, path: Path) -> None:
        """Atomically write the corpus and tokenization needed to rebuild BM25."""
        payload = {
            "version": self._format_version,
            "chunks": [chunk.model_dump(mode="json") for chunk in self._chunks],
            "tokens": self._tokens,
        }
        temporary_path: Path | None = None
        try:
            path.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path, delete=False, suffix=".tmp"
            ) as output:
                temporary_path = Path(output.name)
                json.dump(payload, output, ensure_ascii=False)
            os.replace(temporary_path, path / self._filename)
        except OSError as exc:
            raise IndexBuildError(f"Could not save BM25 index: {exc}") from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def load(self, path: Path) -> None:
        """Validate and restore a saved corpus before replacing the live index."""
        try:
            payload = json.loads((path / self._filename).read_text(encoding="utf-8"))
            if payload["version"] != self._format_version:
                raise ValueError("Unsupported BM25 index version")
            chunks = [DocumentChunk.model_validate(item) for item in payload["chunks"]]
            tokens = payload["tokens"]
            if len(chunks) != len(tokens) or any(
                not isinstance(row, list) or any(not isinstance(token, str) for token in row)
                for row in tokens
            ):
                raise ValueError("Invalid BM25 corpus tokens")
            if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
                raise ValueError("Duplicate chunk_id in saved corpus")
            self._set_corpus(chunks, tokens)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise IndexBuildError(f"Could not load BM25 index: {exc}") from exc

    def _set_corpus(self, chunks: list[DocumentChunk], tokens: list[list[str]]) -> None:
        index = BM25L(tokens) if any(tokens) else None
        self._chunks = list(chunks)
        self._tokens = tokens
        self._index = index
