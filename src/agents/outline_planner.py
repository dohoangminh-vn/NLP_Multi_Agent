"""Outline Planner (TV3) – sinh nhanh khung 4 hoạt động + phân bổ phút + mục tiêu 3 phiếu.

Chạy trước điểm dừng HITL nên phải RẺ: 1 lượt LLM ngắn (hoặc tất định nếu không có LLM).
Luôn bảo đảm tổng phút đúng 45/90: nếu LLM trả sai, phút được lấy theo Class Profiler.
"""

from __future__ import annotations

import json
from typing import List, Optional

from src.agents.profiler import ACTIVITIES, build_profile
from src.prompts import load_prompt
from src.schemas import LessonOutline, LessonRequest
from src.tools.ast_guard import SyllabusRules, _get
from src.rag.ingest import load_yccd
from src.utils.llm import LLMClient, extract_json

_ACT_FOCUS = {
    "Khởi động": "Tình huống/bài toán gợi nhu cầu học {topic}",
    "Hình thành kiến thức": "Tìm hiểu {topic} qua ví dụ SGK",
    "Luyện tập": "Luyện tập {topic} với 3 phiếu phân hoá",
    "Vận dụng": "Vận dụng {topic} giải bài toán thực tế",
}


def deterministic_outline(request: LessonRequest, profile: dict, yccd: List[dict], rules: Optional[SyllabusRules] = None) -> LessonOutline:
    rules = rules or SyllabusRules.load()
    lesson = rules.lesson(request.target_bai) or {}
    topic = request.topic_name or lesson.get("title", f"Bài {request.target_bai}")
    minutes = [a["minutes"] for a in profile["activities"]]
    goals_txt = "; ".join(y["text"].rstrip(". ") for y in yccd[:3]) or lesson.get("summary", "")
    return LessonOutline(
        objective_summary=f"Bài {request.target_bai} – {topic}: {goals_txt}",
        activity_titles=[f"{a}: {_ACT_FOCUS[a].format(topic=topic.lower())}" for a in ACTIVITIES],
        activity_minutes=minutes,
        worksheet_goals=[
            f"Cơ bản (Nhớ/Hiểu): đọc hiểu, trace và điền khuyết code về {topic.lower()}",
            f"Đạt chuẩn (Vận dụng): tự viết chương trình chuẩn SGK dùng {topic.lower()}",
            f"Nâng cao (Phân tích/Sáng tạo): xử lí trường hợp biên / sửa lỗi lôgic với {topic.lower()}",
        ],
        is_approved=False,
    )


class OutlinePlanner:
    def __init__(self, llm: Optional[LLMClient] = None, yccd_loader=load_yccd):
        self.llm = llm
        self.yccd_loader = yccd_loader
        self.rules = SyllabusRules.load()

    def plan(self, request: LessonRequest, profile: Optional[dict] = None, chunks=()) -> LessonOutline:
        profile = profile or build_profile(request)
        yccd = self.yccd_loader(request.target_bai) if self.yccd_loader else []
        base = deterministic_outline(request, profile, yccd, self.rules)
        if self.llm is None:
            return base
        prompt = load_prompt("outline_user").format(
            bai=request.target_bai,
            topic=request.topic_name,
            scope_mode=request.scope_mode,
            minutes=request.target_minutes,
            profile=json.dumps({k: profile[k] for k in ("activities", "emphasis", "lab_notes")}, ensure_ascii=False),
            yccd="\n".join(f"- {y['code']}: {y['text']}" for y in yccd) or "(chưa có)",
            context="\n".join(f"- tr.{_get(c, 'page_number')}: {str(_get(c, 'quoted_text', ''))[:200]}" for c in list(chunks)[:6]),
            skeleton=base.model_dump_json(),
        )
        try:
            data = extract_json(self.llm.complete(load_prompt("outline_system"), prompt).text) or {}
            out = LessonOutline.model_validate({**base.model_dump(), **{k: v for k, v in data.items() if k in LessonOutline.model_fields}})
        except Exception:
            return base
        if len(out.activity_titles) != 4 or len(out.activity_minutes) != 4 or sum(out.activity_minutes) != request.target_minutes:
            out.activity_minutes = base.activity_minutes
            if len(out.activity_titles) != 4:
                out.activity_titles = base.activity_titles
        if len(out.worksheet_goals) != 3:
            out.worksheet_goals = base.worksheet_goals
        out.is_approved = False
        return out

    def __call__(self, state) -> dict:
        req = _get(state, "request")
        req = LessonRequest.model_validate(req) if isinstance(req, dict) else req
        raw = _get(state, "bloom_matrix_json", "") or ""
        profile = json.loads(raw) if raw else build_profile(req)
        return {"outline": self.plan(req, profile, _get(state, "retrieved_chunks", []) or [])}
