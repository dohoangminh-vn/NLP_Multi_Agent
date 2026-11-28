"""Composition root and command line entry point for curriculum ingestion."""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter

from src.rag.chunking.curriculum_mapping import CurriculumSource, MappedCurriculumSource
from src.rag.chunking.hierarchical_chunker import HierarchicalCurriculumChunker
from src.rag.config import RAGConfig
from src.rag.corpus_audit import CorpusManifest
from src.rag.exceptions import IndexBuildError
from src.rag.indexing import PersistentBM25Repository, QdrantVectorRepository
from src.rag.interfaces import (
    BM25IndexRepository,
    DocumentChunker,
    DocumentParser,
    EmbeddingProvider,
    VectorIndexRepository,
)
from src.rag.models import DocumentChunk, IngestionReport
from src.rag.parsers.docling_parser import DoclingDocumentParser
from src.rag.parsers.reviewed_workbook import ReviewedWorkbook, WorkbookSource
from src.rag.taxonomy import TaxonomyRepository
from src.rag.tokenizers import PyViVietnameseTokenizer

logger = logging.getLogger(__name__)


class SentenceTransformerEmbeddingProvider:
    """Load the configured multilingual embedding model only when needed."""

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)

    @property
    def dimension(self) -> int:
        return self._model.get_sentence_embedding_dimension()

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts, normalize_embeddings=True).tolist()

    def embed_query(self, query: str) -> list[float]:
        return self.embed_texts([query])[0]


class IngestionPipeline:
    """Coordinate parsing, chunking and dual indexing through injected interfaces."""

    def __init__(
        self,
        config: RAGConfig,
        parser: DocumentParser,
        chunker: DocumentChunker,
        bm25: BM25IndexRepository,
        vector: VectorIndexRepository | None = None,
        embeddings: EmbeddingProvider | None = None,
        curriculum: CurriculumSource | None = None,
        workbook: WorkbookSource | None = None,
    ) -> None:
        self.config = config
        self.parser = parser
        self.chunker = chunker
        self.bm25 = bm25
        self.vector = vector
        self.embeddings = embeddings
        self.curriculum = curriculum
        self.workbook = workbook

    def run(self, *, dry_run: bool = False, rebuild: bool = False) -> IngestionReport:
        """Read the manifest, create chunks and persist both indexes on success."""
        manifest = CorpusManifest.model_validate_json(self.config.manifest_file.read_text(encoding="utf-8"))
        chunks: list[DocumentChunk] = []
        warnings: list[str] = []
        total_pages = 0
        for document in manifest.documents:
            source = self.config.raw_pdfs_dir / document.filename
            if document.doc_type in {"ct2018", "sbt"}:
                if document.doc_type == "sbt" and self.workbook is not None:
                    pages, mapped_chunks = self.workbook.read(source, document)
                elif document.doc_type == "ct2018" and self.curriculum is not None:
                    page, mapped_chunks = self.curriculum.read(source, document)
                    pages = [page]
                else:
                    raise IndexBuildError(f"Missing reviewed source adapter for {document.doc_type}")
                chunks.extend(mapped_chunks)
                total_pages += len(pages)
                if not dry_run and isinstance(self.parser, DoclingDocumentParser):
                    self.parser.export_pages_to_processed(pages)
                continue
            pages = self.parser.parse_document(
                source, document.page_range.start_page, document.page_range.end_page
            )
            total_pages += len(pages)
            first_lesson_page = next(
                (index for index, page in enumerate(pages) if page.bai_so is not None),
                len(pages),
            )
            usable = pages[first_lesson_page:]
            skipped = len(pages) - len(usable)
            if skipped:
                message = f"{document.doc_id}: skipped {skipped} pages without lesson metadata"
                warnings.append(message)
                logger.warning(message)
            document_chunks = self.chunker.chunk_pages(usable)
            if not document_chunks:
                message = f"{document.doc_id}: no indexable chunks"
                warnings.append(message)
                logger.warning(message)
                if not dry_run:
                    raise IndexBuildError(message)
            chunks.extend(document_chunks)
            if not dry_run and isinstance(self.parser, DoclingDocumentParser):
                self.parser.export_pages_to_processed(usable)
        if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
            raise ValueError("Duplicate chunk IDs across source documents")
        counts = Counter(chunk.metadata.bai_so for chunk in chunks)
        types = Counter(chunk.metadata.loai_noi_dung for chunk in chunks)
        report = IngestionReport(
            total_pages_parsed=total_pages,
            total_chunks_created=len(chunks),
            chunks_by_lesson={lesson: counts.get(lesson, 0) for lesson in range(16, 30)},
            chunks_by_type=dict(types),
            warnings=warnings,
        )
        if dry_run:
            return report
        if self.vector is None or self.embeddings is None:
            raise ValueError("Vector repository and embeddings are required for indexing")
        vectors = self.embeddings.embed_texts([chunk.content for chunk in chunks])
        if rebuild:
            self.vector.clear()
        self.vector.upsert_chunks(chunks, vectors)
        report.vector_indexed = True
        self.bm25.build_index(chunks)
        self.bm25.save(self.config.bm25_index_dir)
        report.bm25_indexed = True
        if isinstance(self.chunker, HierarchicalCurriculumChunker):
            self.chunker.export_chunks_to_jsonl(chunks)
        self.config.processed_dir.mkdir(parents=True, exist_ok=True)
        (self.config.processed_dir / "ingestion_report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return report


def create_pipeline(config: RAGConfig, *, dry_run: bool = False) -> IngestionPipeline:
    """Assemble concrete adapters at one application boundary."""
    taxonomy = TaxonomyRepository.from_json_file(config.taxonomy_file)
    parser = DoclingDocumentParser(config, taxonomy=taxonomy)
    curriculum = MappedCurriculumSource(config.curriculum_mapping_file, taxonomy)
    workbook = ReviewedWorkbook(config.workbook_review_dir, taxonomy, output_dir=config.processed_dir)
    chunker = HierarchicalCurriculumChunker(config, taxonomy)
    bm25 = PersistentBM25Repository(PyViVietnameseTokenizer())
    if dry_run:
        return IngestionPipeline(config, parser, chunker, bm25, curriculum=curriculum, workbook=workbook)
    embeddings = SentenceTransformerEmbeddingProvider(config.embedding_model_name)
    vector = QdrantVectorRepository(
        config.qdrant_storage_path, config.qdrant_collection_name, embeddings.dimension
    )
    return IngestionPipeline(config, parser, chunker, bm25, vector, embeddings, curriculum, workbook)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Topic F curriculum PDFs")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    pipeline = create_pipeline(RAGConfig(), dry_run=args.dry_run)
    try:
        report = pipeline.run(dry_run=args.dry_run, rebuild=args.rebuild)
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    finally:
        if isinstance(pipeline.vector, QdrantVectorRepository):
            pipeline.vector.close()


if __name__ == "__main__":
    main()
