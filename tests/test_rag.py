"""Tầng dữ liệu & Hybrid RAG (TV2)."""

import json

import pytest

from src.rag.ingest import CHUNKS_PATH, YCCD_PATH, load_chunks
from src.tools.ast_guard import ROOT

pytestmark = pytest.mark.skipif(not CHUNKS_PATH.exists(), reason="Chưa chạy python -m src.rag.ingest")


def test_chunks_have_metadata_and_clean_text():
    chunks = load_chunks()
    assert len(chunks) > 150
    assert {c.bai_so for c in chunks} == set(range(16, 30))
    for c in chunks:
        assert c.doc_type == "sgk" and c.page_number > 0 and c.muc and c.loai_noi_dung in {"ly_thuyet", "vi_du", "bai_tap", "yccd"}
        assert "ﬂ" not in c.text and "ﬁ" not in c.text and "/dslash" not in c.text
    text = " ".join(c.text for c in chunks)
    assert "Lệnh range(n) trả lại vùng giá trị" in text and "float(" in text  # tiếng Việt + code đọc đúng


def test_pages_within_lesson_range():
    rules = json.loads((ROOT / "data" / "syllabus_rules.json").read_text(encoding="utf-8"))
    rng = {l["bai"]: l["sgk_pages"] for l in rules["lessons"]}
    for c in load_chunks():
        lo, hi = rng[c.bai_so]
        assert lo <= c.page_number <= hi


def test_yccd_taxonomy_from_sgk():
    data = json.loads(YCCD_PATH.read_text(encoding="utf-8"))
    codes = [k for k in data if not k.startswith("_")]
    assert {int(k.split(".")[1][1:]) for k in codes} == set(range(16, 30))
    assert data["F10.B20.YC1"]["text"] == "Biết được ý nghĩa của vùng giá trị tạo bởi lệnh range()."
    assert all("x" != v["text"][:1] for k, v in data.items() if not k.startswith("_"))


@pytest.fixture(scope="module")
def searcher():
    from src.rag.hybrid_search import HybridSearcher

    return HybridSearcher()


@pytest.mark.parametrize("bai", [16, 19, 22, 25, 29])
def test_no_future_lesson_leak(searcher, bai):
    for q in ["hàm def return", "danh sách append", "xâu kí tự split", "lệnh while", "lỗi chương trình"]:
        for mode in ("bm25", "dense", "hybrid"):
            assert all(h.chunk.bai_so <= bai for h in searcher.search(q, bai, k=10, mode=mode))


def test_retrieval_quality_threshold():
    from eval.retrieval_eval import QUERIES, evaluate
    from src.rag.hybrid_search import HybridSearcher

    queries = json.loads(QUERIES.read_text(encoding="utf-8"))["queries"]
    res = evaluate(HybridSearcher(), queries, "hybrid", True)
    assert res["leaks"] == 0
    assert res["recall_at_5"] >= 0.85 and res["hit_at_5"] >= 0.9


def test_curriculum_retriever_grades_and_filters():
    from src.agents.retriever import CurriculumRetriever
    from src.schemas import LessonRequest

    r = CurriculumRetriever()
    cs = r.retrieve(LessonRequest(target_bai=22, topic_name="Kiểu dữ liệu danh sách"))
    assert cs and all(c.bai_so <= 22 for c in cs)
    assert sum(c.bai_so == 22 for c in cs) >= 3
    assert r.last_meta["grade_ok"]


def test_font_map_committed():
    m = json.loads((ROOT / "data" / "processed" / "font_maps" / "sgk-tin-hoc-10-kntt.json").read_text(encoding="utf-8"))
    assert m["ArialMT"]["55"] == "T"  # GID 55 = 'T' (Arial)
