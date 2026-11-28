"""Qdrant-backed dense index with curriculum scope enforcement."""

from __future__ import annotations

from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient, models

from src.rag.exceptions import IndexBuildError
from src.rag.models import DocumentChunk


class QdrantVectorRepository:
    """Persist validated document chunks and dense vectors in local Qdrant."""

    def __init__(self, path: Path, collection_name: str, dimension: int) -> None:
        if dimension < 1:
            raise ValueError("dimension must be positive")
        path.mkdir(parents=True, exist_ok=True)
        self._client = QdrantClient(path=str(path))
        self._collection = collection_name
        self._dimension = dimension
        if not self._client.collection_exists(collection_name):
            self._client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
            )
        elif self._client.get_collection(collection_name).config.params.vectors.size != dimension:
            raise IndexBuildError("Existing Qdrant collection has a different vector dimension")

    def upsert_chunks(self, chunks: list[DocumentChunk], embeddings: list[list[float]]) -> None:
        """Upsert by stable chunk ID so retries cannot duplicate records."""
        if len(chunks) != len(embeddings):
            raise IndexBuildError("Chunk and embedding counts differ")
        if any(len(vector) != self._dimension for vector in embeddings):
            raise IndexBuildError("Embedding dimension differs from collection dimension")
        points = [
            models.PointStruct(
                id=str(uuid5(NAMESPACE_URL, chunk.chunk_id)),
                vector=vector,
                payload={"bai_so": chunk.metadata.bai_so, "chunk": chunk.model_dump(mode="json")},
            )
            for chunk, vector in zip(chunks, embeddings, strict=True)
        ]
        if points:
            self._client.upsert(collection_name=self._collection, points=points, wait=True)

    def similarity_search(
        self, query_vector: list[float], top_k: int = 5, target_bai: int | None = None
    ) -> list[tuple[DocumentChunk, float]]:
        """Filter in Qdrant and validate every returned chunk after deserialization."""
        if top_k < 1 or len(query_vector) != self._dimension:
            raise ValueError("Invalid top_k or query vector dimension")
        if target_bai is not None and not 16 <= target_bai <= 29:
            raise ValueError("target_bai must be between 16 and 29")
        query_filter = None
        if target_bai is not None:
            query_filter = models.Filter(must=[
                models.FieldCondition(key="bai_so", range=models.Range(lte=target_bai))
            ])
        response = self._client.query_points(
            collection_name=self._collection,
            query=query_vector,
            query_filter=query_filter,
            limit=top_k,
            with_payload=True,
        )
        hits = []
        for point in response.points:
            chunk = DocumentChunk.model_validate(point.payload["chunk"])
            if target_bai is not None:
                chunk.validate_scope(target_bai)
            hits.append((chunk, float(point.score)))
        return hits

    def count(self) -> int:
        return self._client.count(collection_name=self._collection, exact=True).count

    def close(self) -> None:
        """Release the local Qdrant storage lock."""
        self._client.close()

    def clear(self) -> None:
        """Remove indexed points before an explicitly requested rebuild."""
        # Delete points through the storage API. Windows may keep the SQLite file
        # open when a local collection is dropped and recreated under the same name.
        self._client.delete(
            collection_name=self._collection,
            points_selector=models.FilterSelector(filter=models.Filter()),
            wait=True,
        )
        if self.count():
            raise IndexBuildError("Vector index still contains points after clearing")
