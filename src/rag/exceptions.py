"""Custom domain exceptions for the Data & Agentic Hybrid RAG subsystem."""

from __future__ import annotations


class RAGException(Exception):
    """Base exception for all RAG subsystem errors."""


class CorpusNotFoundError(RAGException):
    """Raised when a required corpus PDF or manifest file is not found."""


class ParsingError(RAGException):
    """Raised when parsing or layout extraction fails on a source document."""


class ChunkingError(RAGException):
    """Raised when structural chunking or metadata enrichment fails."""


class IndexBuildError(RAGException):
    """Raised when building, saving, or loading an index (BM25 or Vector) fails."""


class SyllabusScopeViolationError(RAGException):
    """Raised when syllabus scope invariant is violated (e.g. bai_so > target_bai)."""


class TaxonomyError(RAGException):
    """Raised when curriculum taxonomy resolution or validation fails."""
