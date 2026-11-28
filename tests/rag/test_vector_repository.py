"""Qdrant persistence and curriculum filter tests."""

from src.rag.indexing import QdrantVectorRepository
from src.rag.interfaces import VectorIndexRepository

from tests.rag.test_bm25_repository import make_chunk


def test_qdrant_upsert_persistence_and_scope(tmp_path):
    repository = QdrantVectorRepository(tmp_path / "qdrant", "lessons", 2)
    assert isinstance(repository, VectorIndexRepository)
    chunks = [make_chunk("lesson19", 19, "if"), make_chunk("lesson21", 21, "while")]
    vectors = [[1.0, 0.0], [0.0, 1.0]]
    repository.upsert_chunks(chunks, vectors)
    repository.upsert_chunks(chunks, vectors)
    assert repository.count() == 2
    assert [chunk.chunk_id for chunk, _ in repository.similarity_search([0.0, 1.0], target_bai=19)] == ["lesson19"]
    repository.close()

    reopened = QdrantVectorRepository(tmp_path / "qdrant", "lessons", 2)
    assert reopened.count() == 2
    assert reopened.similarity_search([0.0, 1.0], target_bai=20)[0][0].metadata.bai_so <= 20
    reopened.close()


def test_clear_replaces_corpus_and_persists_without_stale_points(tmp_path):
    path = tmp_path / "qdrant"
    repository = QdrantVectorRepository(path, "lessons", 2)
    repository.upsert_chunks([make_chunk("old", 19, "old")], [[1.0, 0.0]])
    repository.clear()
    assert repository.count() == 0
    repository.upsert_chunks([make_chunk("new", 21, "new")], [[0.0, 1.0]])
    repository.close()
    reopened = QdrantVectorRepository(path, "lessons", 2)
    assert reopened.count() == 1
    assert reopened.similarity_search([0.0, 1.0])[0][0].chunk_id == "new"
    reopened.close()
