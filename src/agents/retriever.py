"""Curriculum Retriever (TV2) – node truy xuất + Document Grader + Query Rewriter.

1. Sinh truy vấn từ yêu cầu: tên bài + từng YCCĐ + "ví dụ"/"luyện tập" (đa truy vấn).
2. Hybrid Search lọc cứng bai_so <= target_bai, gộp kết quả (RRF theo truy vấn), khử trùng lặp.
3. Grader (tất định): đủ chunk của bài mục tiêu? có lí thuyết/ví dụ? có bài tập? mỗi YCCĐ có chunk phủ?
4. Thiếu → Query Rewriter (LLM nếu có, không thì mở rộng từ khoá theo bài) và truy hồi lại (≤ 2 lần).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src.rag.hybrid_search import HybridSearcher, SearchHit
from src.schemas import LessonRequest, SourceCitation
from src.tools.ast_guard import SyllabusRules, _get
from src.tools.pedagogy_checks import load_yccd
from src.utils.llm import LLMClient, extract_json

MAX_REWRITES = 2


@dataclass
class GradeResult:
    ok: bool
    missing: List[str] = field(default_factory=list)


class CurriculumRetriever:
    def __init__(self, searcher: Optional[HybridSearcher] = None, llm: Optional[LLMClient] = None,
                 top_n: int = 8, per_query_k: int = 5, yccd_loader=load_yccd):
        self._searcher = searcher
        self.llm = llm
        self.top_n = top_n
        self.k = per_query_k
        self.yccd_loader = yccd_loader
        self.rules = SyllabusRules.load()
        self.last_meta: dict = {}

    @property
    def searcher(self) -> HybridSearcher:
        if self._searcher is None:  # nạp lười: chỉ dựng index khi cần
            self._searcher = HybridSearcher()
        return self._searcher

    # ---------------- truy vấn ----------------

    def base_queries(self, req: LessonRequest) -> List[str]:
        lesson = self.rules.lesson(req.target_bai) or {}
        topic = req.topic_name or lesson.get("title", "")
        qs = [topic, f"{topic} ví dụ", f"{topic} thực hành luyện tập"]
        qs += [y["text"] for y in self.yccd_loader(req.target_bai)]
        return [q for q in qs if q]

    def rewrite(self, req: LessonRequest, missing: List[str]) -> List[str]:
        lesson = self.rules.lesson(req.target_bai) or {}
        if self.llm is not None:
            prompt = (
                f"Bài {req.target_bai} – {lesson.get('title', '')} (SGK Tin học 10 KNTT). Truy hồi còn thiếu: {', '.join(missing)}.\n"
                "Viết 3 truy vấn tìm kiếm ngắn bằng tiếng Việt (dùng đúng thuật ngữ SGK) để bổ sung. "
                'Trả JSON: {"queries": ["...", "...", "..."]}'
            )
            try:
                data = extract_json(self.llm.complete("Bạn là trợ lý tìm kiếm học liệu.", prompt).text) or {}
                qs = [str(q) for q in data.get("queries", []) if q]
                if qs:
                    return qs[:3]
            except Exception:
                pass
        summary = lesson.get("summary", "")
        return [f"{lesson.get('title', '')} {m}" for m in missing] + ([summary] if summary else [])

    # ---------------- grader ----------------

    def grade(self, req: LessonRequest, hits: List[SearchHit]) -> GradeResult:
        own = [h for h in hits if h.chunk.bai_so == req.target_bai]
        missing = []
        if len(own) < 3:
            missing.append("nội dung của chính bài học")
        if not any(h.chunk.loai_noi_dung in ("ly_thuyet", "vi_du") for h in own):
            missing.append("lí thuyết và ví dụ")
        if not any(h.chunk.loai_noi_dung == "bai_tap" for h in own):
            missing.append("bài tập luyện tập/vận dụng")
        text = " ".join(h.chunk.text.lower() for h in own)
        for y in self.yccd_loader(req.target_bai):
            words = [w for w in y["text"].lower().replace(".", " ").split() if len(w) > 3]
            if words and sum(w in text for w in words) / len(words) < 0.5:
                missing.append(f"YCCĐ {y['code']}")
        return GradeResult(ok=not missing, missing=missing)

    # ---------------- chạy ----------------

    def _search_many(self, queries: List[str], bai: int, scores: Dict[str, float], store: Dict[str, SearchHit]) -> None:
        for q in queries:
            for h in self.searcher.search(q, bai, k=self.k):
                cid = h.chunk.chunk_id
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (60 + h.rank)
                store.setdefault(cid, h)

    def retrieve_hits(self, req: LessonRequest) -> List[SearchHit]:
        scores: Dict[str, float] = {}
        store: Dict[str, SearchHit] = {}
        queries = self.base_queries(req)
        self._search_many(queries, req.target_bai, scores, store)
        rewrites, grade = 0, None
        while True:
            ranked = sorted(store.values(), key=lambda h: scores[h.chunk.chunk_id], reverse=True)
            # ưu tiên chunk của bài mục tiêu, giữ tối đa 2 chunk kế thừa từ bài trước
            own = [h for h in ranked if h.chunk.bai_so == req.target_bai]
            prev = [h for h in ranked if h.chunk.bai_so < req.target_bai][:2]
            top = (own[: self.top_n - len(prev)] + prev)[: self.top_n]
            grade = self.grade(req, top)
            if grade.ok or rewrites >= MAX_REWRITES:
                break
            rewrites += 1
            new_q = self.rewrite(req, grade.missing)
            queries += new_q
            self._search_many(new_q, req.target_bai, scores, store)
        assert all(h.chunk.bai_so <= req.target_bai for h in top), "Lọt chunk vượt bài!"
        self.last_meta = {"rewrites": rewrites, "queries": queries, "grade_ok": grade.ok, "missing": grade.missing}
        return top

    def retrieve(self, request: LessonRequest) -> List[SourceCitation]:
        return [SourceCitation.model_validate(h.to_citation()) for h in self.retrieve_hits(request)]

    def __call__(self, state) -> dict:
        req = _get(state, "request")
        req = LessonRequest.model_validate(req) if isinstance(req, dict) else req
        return {"retrieved_chunks": self.retrieve(req), "retrieval_meta": json.dumps(self.last_meta, ensure_ascii=False)}


_SHARED: Optional[CurriculumRetriever] = None


def build_benchmark_retriever() -> CurriculumRetriever:
    """Factory cho `eval/run_benchmark.py --retriever src.agents.retriever:build_benchmark_retriever`."""
    global _SHARED
    if _SHARED is None:
        _SHARED = CurriculumRetriever()
    return _SHARED
