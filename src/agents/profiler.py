"""Class Profiler (TV3) – ánh xạ hồ sơ lớp → ma trận Bloom, năng lực Tin học, khung phút.

Tất định (không gọi LLM) để chạy song song với Retriever và luôn ổn định khi benchmark.
Đầu ra ghi vào `AgentState.bloom_matrix_json` (chuỗi JSON).
"""

from __future__ import annotations

import json
from typing import Dict, List

from src.schemas import LessonRequest
from src.tools.ast_guard import _get

ACTIVITIES = ["Khởi động", "Hình thành kiến thức", "Luyện tập", "Vận dụng"]

# Khung phút chuẩn theo chế độ tiết (tổng đúng 45/90)
BASE_MINUTES: Dict[str, List[int]] = {
    "45m_theory": [5, 20, 12, 8],
    "45m_practice": [5, 10, 20, 10],
    "90m_full": [10, 30, 32, 18],
}

# Năng lực đặc thù Tin học – Chương trình GDPT 2018 môn Tin học
CS_COMPETENCIES = {
    "NLa": "Sử dụng và quản lí các phương tiện công nghệ thông tin và truyền thông",
    "NLb": "Ứng xử phù hợp trong môi trường số",
    "NLc": "Giải quyết vấn đề với sự hỗ trợ của công nghệ thông tin và truyền thông",
    "NLd": "Ứng dụng công nghệ thông tin và truyền thông trong học và tự học",
    "NLe": "Hợp tác trong môi trường số",
}

TIERS = [
    {
        "tier": "Cơ bản",
        "bloom": ["Nhớ", "Hiểu"],
        "competencies": ["NLa", "NLc"],
        "task_types": ["Đọc hiểu code", "Lập bảng trace giá trị biến", "Điền khuyết 1–2 dòng (___)"],
        "support": "Có code mẫu, gợi ý từng bước, ví dụ input/output cụ thể",
    },
    {
        "tier": "Đạt chuẩn",
        "bloom": ["Vận dụng"],
        "competencies": ["NLc"],
        "task_types": ["Tự viết chương trình giải bài toán chuẩn SGK"],
        "support": "Có đặc tả input/output và 1 ví dụ",
    },
    {
        "tier": "Nâng cao",
        "bloom": ["Phân tích", "Sáng tạo"],
        "competencies": ["NLc", "NLd"],
        "task_types": ["Xử lí trường hợp biên", "Tìm và sửa lỗi lôgic", "Cải tiến thuật toán trong phạm vi bài"],
        "support": "Chỉ nêu yêu cầu, HS tự thiết kế test",
    },
]


def target_minutes_for(scope_mode: str) -> int:
    return 90 if scope_mode == "90m_full" else 45


def allocate_minutes(request: LessonRequest) -> List[int]:
    minutes = list(BASE_MINUTES.get(request.scope_mode, BASE_MINUTES["45m_theory"]))
    shift = 3 if request.target_minutes >= 90 else 2
    # Lớp nhiều HS yếu: thêm thời gian hình thành kiến thức; lớp khá giỏi: thêm thời gian vận dụng
    if request.basic_ratio >= 0.45 and minutes[3] - shift >= 5:
        minutes[1] += shift
        minutes[3] -= shift
    elif request.advanced_ratio >= 0.35 and minutes[1] - shift >= 8:
        minutes[1] -= shift
        minutes[3] += shift
    diff = request.target_minutes - sum(minutes)
    minutes[2] += diff  # luôn khớp tổng
    return minutes


def normalize_ratios(request: LessonRequest) -> List[float]:
    r = [max(0.0, request.basic_ratio), max(0.0, request.standard_ratio), max(0.0, request.advanced_ratio)]
    s = sum(r) or 1.0
    return [round(x / s, 4) for x in r]


def build_profile(request: LessonRequest) -> dict:
    ratios = normalize_ratios(request)
    minutes = allocate_minutes(request)
    tiers = []
    for t, ratio in zip(TIERS, ratios):
        tiers.append({**t, "ratio": ratio, "competency_text": [f"{c}: {CS_COMPETENCIES[c]}" for c in t["competencies"]]})
    dominant = max(range(3), key=lambda i: ratios[i])
    emphasis = [
        "Lớp nhiều HS cần hỗ trợ: tăng ví dụ mẫu, bảng trace, làm mẫu trước khi giao bài.",
        "Lớp đại trà: cân bằng giữa làm mẫu và tự luyện tập.",
        "Lớp khá giỏi: rút ngắn làm mẫu, tăng bài toán mở và trường hợp biên.",
    ][dominant]
    lab = request.lab_constraints or ""
    lab_notes = []
    if "2 HS" in lab or "2 hs" in lab.lower():
        lab_notes.append("2 HS/1 máy: tổ chức theo cặp (1 HS gõ, 1 HS kiểm tra), đổi vai giữa các phiếu.")
    if "không" in lab.lower() and "máy chiếu" in lab.lower():
        lab_notes.append("Không có máy chiếu: in sẵn code mẫu trên phiếu, GV viết bảng.")
    return {
        "target_bai": request.target_bai,
        "scope_mode": request.scope_mode,
        "target_minutes": request.target_minutes,
        "activities": [{"activity": a, "minutes": m} for a, m in zip(ACTIVITIES, minutes)],
        "tiers": tiers,
        "emphasis": emphasis,
        "lab_notes": lab_notes,
        "general_competencies": ["Tự chủ và tự học", "Giao tiếp và hợp tác", "Giải quyết vấn đề và sáng tạo"],
        "qualities": ["Chăm chỉ", "Trách nhiệm", "Trung thực"],
    }


class ClassProfiler:
    """Node LangGraph: state → {"bloom_matrix_json": str}."""

    def __call__(self, state) -> dict:
        req = _get(state, "request")
        if isinstance(req, dict):
            req = LessonRequest.model_validate(req)
        return {"bloom_matrix_json": json.dumps(build_profile(req), ensure_ascii=False)}
