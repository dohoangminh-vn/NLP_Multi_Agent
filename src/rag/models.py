"""Domain data models and DTOs for the RAG subsystem."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from src.rag.exceptions import SyllabusScopeViolationError

DocType = Literal["ct2018", "sgk", "sbt"]
ContentType = Literal["ly_thuyet", "vi_du", "bai_tap", "yccd"]


class SourceCitation(BaseModel):
    """Citation referencing a specific section and page of curriculum material.

    Matches the SourceCitation contract specified in thietke.md Section 4.1.
    """

    doc_name: str = Field(..., min_length=1)
    bai_so: int = Field(..., ge=16, le=29)
    section_title: str = Field(..., min_length=1)
    page_number: int = Field(..., ge=1)
    quoted_text: str = Field(..., min_length=1)


class ChunkMetadata(BaseModel):
    """Metadata attached to every DocumentChunk for filtering and attribution."""

    doc_type: DocType
    doc_name: str = Field(..., min_length=1)
    chuong: str = "Chủ đề F: Giải quyết vấn đề với sự trợ giúp của máy tính"
    bai_so: int = Field(..., ge=16, le=29)
    ten_bai: str = Field(..., min_length=1)
    muc: str = Field(..., min_length=1)
    page_number: int = Field(..., ge=1)
    loai_noi_dung: ContentType
    yccd_ids: list[str] = Field(default_factory=list)


class DocumentChunk(BaseModel):
    """Atomic unit of curriculum content with metadata and citation export."""

    chunk_id: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    metadata: ChunkMetadata
    token_count: int = Field(default=0, ge=0)

    def to_source_citation(self, quoted_text: str | None = None) -> SourceCitation:
        """Convert chunk into a standardized SourceCitation for AgentState."""
        quote = quoted_text.strip() if quoted_text else self.content.strip()[:300]
        return SourceCitation(
            doc_name=self.metadata.doc_name,
            bai_so=self.metadata.bai_so,
            section_title=self.metadata.muc,
            page_number=self.metadata.page_number,
            quoted_text=quote,
        )

    def validate_scope(self, target_bai: int) -> None:
        """Assert defense-in-depth: chunk lesson must not exceed target lesson."""
        if self.metadata.bai_so > target_bai:
            raise SyllabusScopeViolationError(
                f"Syllabus scope violation: chunk from lesson {self.metadata.bai_so} "
                f"exceeds target lesson {target_bai}."
            )


class ParsedPage(BaseModel):
    """Page extracted from PDF by layout-aware parser."""

    doc_type: DocType
    doc_name: str = Field(..., min_length=1)
    page_number: int = Field(..., ge=1)
    markdown_content: str = Field(..., min_length=1)
    bai_so: int | None = Field(default=None, ge=16, le=29)
    ten_bai: str | None = None
    has_tables: bool = False
    has_code_blocks: bool = False


class IngestionReport(BaseModel):
    """Aggregated ingestion execution statistics and validation results."""

    total_pages_parsed: int = Field(default=0, ge=0)
    total_chunks_created: int = Field(default=0, ge=0)
    chunks_by_lesson: dict[int, int] = Field(default_factory=dict)
    chunks_by_type: dict[ContentType, int] = Field(default_factory=dict)
    bm25_indexed: bool = False
    vector_indexed: bool = False
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_totals(self) -> IngestionReport:
        if self.chunks_by_lesson and sum(self.chunks_by_lesson.values()) != self.total_chunks_created:
            raise ValueError("Sum of chunks_by_lesson must equal total_chunks_created.")
        return self
