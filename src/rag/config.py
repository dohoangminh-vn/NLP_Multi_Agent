"""Central configuration for RAG ingestion, indexing, and search."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RAGConfig(BaseSettings):
    """Configuration settings for the RAG subsystem.

    Values can be overridden using environment variables with prefix 'RAG_'.
    """

    model_config = SettingsConfigDict(env_prefix="RAG_", extra="ignore")

    project_root: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2])

    # Data paths
    raw_pdfs_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[2] / "data" / "raw_pdfs"
    )
    processed_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[2] / "data" / "processed"
    )
    indexes_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[2] / "data" / "indexes"
    )
    taxonomy_file: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[2]
        / "data"
        / "yccd_taxonomy.json"
    )
    manifest_file: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[2]
        / "data"
        / "raw_pdfs"
        / "manifest.json"
    )

    # Chunking parameters
    default_chunk_size: int = 500
    default_chunk_overlap: int = 50

    # Index parameters
    bm25_index_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[2]
        / "data"
        / "indexes"
        / "bm25"
    )
    qdrant_storage_path: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[2]
        / "data"
        / "indexes"
        / "qdrant"
    )
    qdrant_collection_name: str = "cs10_curriculum"
    embedding_model_name: str = "bkai-foundation-models/vietnamese-bi-encoder"
    reranker_model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # Domain bounds (Topic F: Lessons 16-29)
    min_lesson: int = 16
    max_lesson: int = 29
