"""Pipeline integration with fake parser, chunker and embeddings."""

import json

from src.rag.config import RAGConfig
from src.rag.indexing import PersistentBM25Repository, QdrantVectorRepository
from src.rag.ingest import IngestionPipeline
from src.rag.models import ParsedPage
from src.rag.tokenizers import PyViVietnameseTokenizer

from tests.rag.test_bm25_repository import make_chunk


class FakeParser:
    def parse_document(self, file_path, start_page=None, end_page=None):
        return [ParsedPage(doc_type="sgk", doc_name="SGK", page_number=1,
                           markdown_content="Bài 19: if", bai_so=19)]


class FakeChunker:
    def chunk_pages(self, pages):
        return [make_chunk("example", 19, "Câu lệnh if")]


class FakeEmbeddings:
    dimension = 2

    def embed_texts(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, query):
        return [1.0, 0.0]


def test_pipeline_dry_run_and_persistent_dual_index(tmp_path):
    manifest = {"version": "1", "scope": "F", "documents": [
        {"doc_id": "sgk", "doc_type": "sgk", "doc_name": "SGK", "filename": "book.pdf",
         "page_range": {"start_page": 1, "end_page": 1}}
    ]}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    config = RAGConfig(manifest_file=manifest_path, raw_pdfs_dir=tmp_path,
                       bm25_index_dir=tmp_path / "bm25", processed_dir=tmp_path / "processed")
    bm25 = PersistentBM25Repository(PyViVietnameseTokenizer())
    vector = QdrantVectorRepository(tmp_path / "qdrant", "lesson", 2)
    pipeline = IngestionPipeline(config, FakeParser(), FakeChunker(), bm25, vector, FakeEmbeddings())
    dry_report = pipeline.run(dry_run=True)
    assert dry_report.total_pages_parsed == 1
    assert not dry_report.bm25_indexed
    report = pipeline.run()
    assert report.total_chunks_created == 1
    assert report.chunks_by_lesson[19] == 1
    assert report.bm25_indexed and report.vector_indexed
    pipeline.run()
    assert vector.count() == 1
    assert bm25.search("if", target_bai=19)
    vector.close()
