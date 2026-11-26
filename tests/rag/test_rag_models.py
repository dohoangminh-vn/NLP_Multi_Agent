"""Unit tests for RAG core domain models, configuration, exceptions, and interfaces."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from src.rag.config import RAGConfig
from src.rag.exceptions import (
    ChunkingError,
    CorpusNotFoundError,
    IndexBuildError,
    ParsingError,
    RAGException,
    SyllabusScopeViolationError,
    TaxonomyError,
)
from src.rag.interfaces import (
    BM25IndexRepository,
    DocumentChunker,
    DocumentParser,
    EmbeddingProvider,
    VectorIndexRepository,
    VietnameseTokenizer,
)
from src.rag.models import (
    ChunkMetadata,
    DocumentChunk,
    IngestionReport,
    ParsedPage,
    SourceCitation,
)


def test_chunk_metadata_valid() -> None:
    meta = ChunkMetadata(
        doc_type="sgk",
        doc_name="SGK Tin hoc 10 KNTT",
        bai_so=20,
        ten_bai="Câu lệnh lặp For",
        muc="1. Lệnh for",
        page_number=106,
        loai_noi_dung="ly_thuyet",
        yccd_ids=["F10.B20.YC1"],
    )
    assert meta.bai_so == 20
    assert meta.page_number == 106
    assert meta.doc_type == "sgk"
    assert "Chủ đề F" in meta.chuong


def test_chunk_metadata_invalid_page_number() -> None:
    with pytest.raises(ValidationError):
        ChunkMetadata(
            doc_type="sgk",
            doc_name="SGK Tin hoc 10 KNTT",
            bai_so=20,
            ten_bai="Câu lệnh lặp For",
            muc="1. Lệnh for",
            page_number=0,  # Invalid: page_number must be >= 1
            loai_noi_dung="ly_thuyet",
        )


def test_chunk_metadata_invalid_lesson() -> None:
    with pytest.raises(ValidationError):
        ChunkMetadata(
            doc_type="sgk",
            doc_name="SGK Tin hoc 10 KNTT",
            bai_so=15,  # Invalid: must be >= 16
            ten_bai="Out of scope",
            muc="1. Intro",
            page_number=80,
            loai_noi_dung="ly_thuyet",
        )

    with pytest.raises(ValidationError):
        ChunkMetadata(
            doc_type="sgk",
            doc_name="SGK Tin hoc 10 KNTT",
            bai_so=30,  # Invalid: must be <= 29
            ten_bai="Out of scope",
            muc="1. Advanced",
            page_number=155,
            loai_noi_dung="ly_thuyet",
        )


def test_chunk_metadata_invalid_doc_type() -> None:
    with pytest.raises(ValidationError):
        ChunkMetadata(
            doc_type="invalid_type",  # type: ignore[arg-type]
            doc_name="SGK",
            bai_so=20,
            ten_bai="For",
            muc="Muc 1",
            page_number=100,
            loai_noi_dung="ly_thuyet",
        )


def test_document_chunk_to_source_citation() -> None:
    meta = ChunkMetadata(
        doc_type="sgk",
        doc_name="SGK Tin học 10 KNTT",
        bai_so=19,
        ten_bai="Câu lệnh điều kiện If",
        muc="2. Lệnh if-else",
        page_number=103,
        loai_noi_dung="ly_thuyet",
        yccd_ids=["F10.B19.YC2"],
    )
    chunk = DocumentChunk(
        chunk_id="sgk_b19_p103_c01",
        content="Cú pháp câu lệnh rẽ nhánh if-else trong Python: if <điều kiện>: <khối lệnh 1> else: <khối lệnh 2>",
        metadata=meta,
        token_count=35,
    )

    citation = chunk.to_source_citation()
    assert isinstance(citation, SourceCitation)
    assert citation.doc_name == "SGK Tin học 10 KNTT"
    assert citation.bai_so == 19
    assert citation.section_title == "2. Lệnh if-else"
    assert citation.page_number == 103
    assert "Cú pháp câu lệnh" in citation.quoted_text

    # Custom quote override
    custom_citation = chunk.to_source_citation(quoted_text="if-else syntax")
    assert custom_citation.quoted_text == "if-else syntax"


def test_document_chunk_scope_validation() -> None:
    meta = ChunkMetadata(
        doc_type="sgk",
        doc_name="SGK Tin học 10 KNTT",
        bai_so=21,
        ten_bai="Câu lệnh lặp While",
        muc="1. Lệnh while",
        page_number=109,
        loai_noi_dung="ly_thuyet",
    )
    chunk = DocumentChunk(
        chunk_id="sgk_b21_p109_c01",
        content="Cú pháp while <điều kiện>:",
        metadata=meta,
    )

    # Valid when target is 21 or higher
    chunk.validate_scope(target_bai=21)
    chunk.validate_scope(target_bai=25)

    # Violation when target is 20 (leaking while into for lesson)
    with pytest.raises(SyllabusScopeViolationError, match="exceeds target lesson 20"):
        chunk.validate_scope(target_bai=20)


def test_parsed_page_model() -> None:
    page = ParsedPage(
        doc_type="sgk",
        doc_name="SGK Tin hoc 10 KNTT",
        page_number=106,
        markdown_content="# BÀI 20: CÂU LỆNH LẶP FOR\n\n```python\nfor i in range(10):\n    print(i)\n```",
        bai_so=20,
        ten_bai="Câu lệnh lặp For",
        has_tables=False,
        has_code_blocks=True,
    )
    assert page.page_number == 106
    assert page.has_code_blocks is True
    assert page.bai_so == 20

    with pytest.raises(ValidationError):
        ParsedPage(
            doc_type="sgk",
            doc_name="SGK",
            page_number=0,  # Invalid
            markdown_content="Test",
        )


def test_ingestion_report_totals_validation() -> None:
    report = IngestionReport(
        total_pages_parsed=50,
        total_chunks_created=10,
        chunks_by_lesson={16: 4, 17: 6},
        chunks_by_type={"ly_thuyet": 6, "bai_tap": 4},
        bm25_indexed=True,
        vector_indexed=True,
    )
    assert report.total_chunks_created == 10
    assert report.bm25_indexed is True

    # Inconsistent totals should raise ValueError
    with pytest.raises(ValidationError, match="total_chunks_created"):
        IngestionReport(
            total_pages_parsed=50,
            total_chunks_created=10,
            chunks_by_lesson={16: 4, 17: 10},  # Sum is 14 != 10
        )


def test_rag_config_defaults() -> None:
    cfg = RAGConfig()
    assert cfg.min_lesson == 16
    assert cfg.max_lesson == 29
    assert cfg.default_chunk_size == 500
    assert cfg.qdrant_collection_name == "cs10_curriculum"
    assert cfg.raw_pdfs_dir.is_dir()
    assert cfg.taxonomy_file.name == "yccd_taxonomy.json"


def test_exceptions_hierarchy() -> None:
    assert issubclass(CorpusNotFoundError, RAGException)
    assert issubclass(ParsingError, RAGException)
    assert issubclass(ChunkingError, RAGException)
    assert issubclass(IndexBuildError, RAGException)
    assert issubclass(SyllabusScopeViolationError, RAGException)
    assert issubclass(TaxonomyError, RAGException)


def test_protocol_runtime_checkable() -> None:
    class DummyParser:
        def parse_document(
            self,
            file_path: Path,
            start_page: int | None = None,
            end_page: int | None = None,
        ) -> list[ParsedPage]:
            return []

    class DummyChunker:
        def chunk_pages(self, pages: list[ParsedPage]) -> list[DocumentChunk]:
            return []

    class DummyTokenizer:
        def tokenize(self, text: str) -> list[str]:
            return text.split()

        def segment_words(self, text: str) -> str:
            return text

    class DummyEmbeddingProvider:
        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            return [[0.1] * 384 for _ in texts]

        def embed_query(self, query: str) -> list[float]:
            return [0.1] * 384

        @property
        def dimension(self) -> int:
            return 384

    assert isinstance(DummyParser(), DocumentParser)
    assert isinstance(DummyChunker(), DocumentChunker)
    assert isinstance(DummyTokenizer(), VietnameseTokenizer)
    assert isinstance(DummyEmbeddingProvider(), EmbeddingProvider)
    assert not isinstance(DummyParser(), BM25IndexRepository)
    assert not isinstance(DummyParser(), VectorIndexRepository)
