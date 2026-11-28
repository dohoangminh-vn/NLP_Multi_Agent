"""Tokenization and persistent BM25 retrieval contracts."""

from __future__ import annotations

import json

import pytest
from src.rag.exceptions import IndexBuildError
from src.rag.indexing import PersistentBM25Repository
from src.rag.interfaces import BM25IndexRepository, VietnameseTokenizer
from src.rag.models import ChunkMetadata, DocumentChunk
from src.rag.tokenizers import PyViVietnameseTokenizer


def make_chunk(chunk_id: str, lesson: int, content: str) -> DocumentChunk:
    return DocumentChunk(
        chunk_id=chunk_id,
        content=content,
        metadata=ChunkMetadata(
            doc_type="sgk",
            doc_name="Tin học 10 KNTT",
            bai_so=lesson,
            ten_bai=f"Bài {lesson}",
            muc="Mục 1",
            page_number=lesson,
            loai_noi_dung="ly_thuyet",
        ),
    )


def test_tokenizer_preserves_curriculum_terms_and_python_code():
    tokenizer = PyViVietnameseTokenizer()
    assert isinstance(tokenizer, VietnameseTokenizer)
    text = (
        "câu lệnh rẽ nhánh, xâu kí tự, danh sách, chương trình con. "
        "`for item in range(3):`\n```python\nif item:\n    print(item)\n```"
    )
    segmented = tokenizer.segment_words(text)
    for term in ("câu_lệnh", "rẽ_nhánh", "xâu_kí_tự", "danh_sách", "chương_trình_con"):
        assert term in segmented
    assert "`for item in range(3):`" in segmented
    assert "```python\nif item:\n    print(item)\n```" in segmented
    assert {"for", "while", "range", "if", "def"} <= set(
        tokenizer.tokenize("for while range if def")
    )
    assert "for_loop" in tokenizer.tokenize("for_loop")


def test_bm25_scope_ranking_and_persistence(tmp_path):
    tokenizer = PyViVietnameseTokenizer()
    repository = PersistentBM25Repository(tokenizer)
    assert isinstance(repository, BM25IndexRepository)
    repository.build_index([
        make_chunk("b19", 19, "Câu lệnh rẽ nhánh if kiểm tra điều kiện"),
        make_chunk("b20", 20, "Câu lệnh lặp for với range"),
        make_chunk("b21", 21, "Câu lệnh lặp for với range và while"),
    ])
    before = repository.search("for range", top_k=3, target_bai=20)
    assert before
    assert all(chunk.metadata.bai_so <= 20 for chunk, _ in before)
    assert before[0][0].chunk_id == "b20"
    assert before[0][1] > 0
    assert repository.search("while", target_bai=20) == []

    repository.save(tmp_path)
    payload = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert all(isinstance(chunk["metadata"]["bai_so"], int) for chunk in payload["chunks"])
    restored = PersistentBM25Repository(tokenizer)
    restored.load(tmp_path)
    assert restored.search("for range", top_k=3, target_bai=20) == before
    assert all(chunk.metadata.bai_so <= 19 for chunk, _ in restored.search("câu lệnh", target_bai=19))


def test_bm25_rejects_invalid_inputs_and_corrupt_index(tmp_path):
    repository = PersistentBM25Repository(PyViVietnameseTokenizer())
    repository.build_index([make_chunk("one", 19, "câu lệnh if")])
    with pytest.raises(ValueError, match="top_k"):
        repository.search("if", top_k=0)
    with pytest.raises(ValueError, match="target_bai"):
        repository.search("if", target_bai=30)
    with pytest.raises(IndexBuildError, match="Duplicate"):
        repository.build_index([make_chunk("one", 19, "if"), make_chunk("one", 20, "for")])
    repository.save(tmp_path)
    (tmp_path / "index.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(IndexBuildError, match="Could not load"):
        repository.load(tmp_path)
    assert repository.search("if", target_bai=19)
