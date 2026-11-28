"""Abstract interfaces (Protocols) for parser, chunker, tokenizer, and index repositories."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from src.rag.models import DocumentChunk, ParsedPage


@runtime_checkable
class DocumentParser(Protocol):
    """Strategy for layout-aware PDF document parsing."""

    def parse_document(
        self,
        file_path: Path,
        start_page: int | None = None,
        end_page: int | None = None,
    ) -> list[ParsedPage]:
        """Parse PDF pages within range into Markdown with layout preservation."""
        ...


@runtime_checkable
class DocumentChunker(Protocol):
    """Strategy for hierarchical curriculum chunking and metadata enrichment."""

    def chunk_pages(self, pages: list[ParsedPage]) -> list[DocumentChunk]:
        """Split parsed Markdown pages into structured chunks without breaking code/tables."""
        ...


@runtime_checkable
class VietnameseTokenizer(Protocol):
    """Strategy for Vietnamese word segmentation with programming token preservation."""

    def tokenize(self, text: str) -> list[str]:
        """Tokenize text into search tokens, preserving Python keywords and symbols."""
        ...

    def segment_words(self, text: str) -> str:
        """Join compound words with underscores while leaving code tokens intact."""
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Interface for generating dense vector embeddings."""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Batch generate embeddings for multiple document chunks."""
        ...

    def embed_query(self, query: str) -> list[float]:
        """Generate embedding vector for a search query."""
        ...

    @property
    def dimension(self) -> int:
        """Return embedding vector dimension."""
        ...


@runtime_checkable
class BM25IndexRepository(Protocol):
    """Repository for building, persisting, and querying BM25 index with syllabus filters."""

    def build_index(self, chunks: list[DocumentChunk]) -> None:
        """Build BM25 sparse index over document chunks."""
        ...

    def search(
        self,
        query: str,
        top_k: int = 5,
        target_bai: int | None = None,
    ) -> list[tuple[DocumentChunk, float]]:
        """Query index and return top_k chunks with scores, enforcing bai_so <= target_bai."""
        ...

    def save(self, path: Path) -> None:
        """Persist BM25 index and chunk corpus to disk."""
        ...

    def load(self, path: Path) -> None:
        """Load persisted BM25 index from disk."""
        ...


@runtime_checkable
class VectorIndexRepository(Protocol):
    """Repository for dense vector storage (Qdrant) with metadata filtering."""

    def upsert_chunks(
        self,
        chunks: list[DocumentChunk],
        embeddings: list[list[float]],
    ) -> None:
        """Upsert chunks and embeddings into collection."""
        ...

    def similarity_search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        target_bai: int | None = None,
    ) -> list[tuple[DocumentChunk, float]]:
        """Dense similarity search with metadata filter bai_so <= target_bai."""
        ...

    def count(self) -> int:
        """Return total number of vectors in collection."""
        ...

    def clear(self) -> None:
        """Remove existing vectors before an explicitly requested rebuild."""
        ...
