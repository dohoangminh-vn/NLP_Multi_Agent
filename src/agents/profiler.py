"""TV3 deterministic class profiling; compatible with provisional graph contract."""
from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from src.utils.yccd_adapter import DEFAULT_TAXONOMY, read_yccd

from src.utils.pedagogy_validator import (
    ACTIVITIES as PHASES, SCOPE_MINUTES, PedagogyValidationError,
    validate_lesson_request, validate_activity_minutes,
)

ACTIVITIES = list(PHASES)
BASE_MINUTES = {
    "45m_theory": (5, 20, 12, 8),
    "45m_practice": (5, 10, 20, 10),
    "90m_full": (10, 30, 32, 18),
}
CS_COMPETENCIES = {
    "NLa": "Sử dụng và quản lí các phương tiện công nghệ thông tin và truyền thông",
    "NLb": "Ứng xử phù hợp trong môi trường số",
    "NLc": "Giải quyết vấn đề với sự hỗ trợ của công nghệ thông tin và truyền thông",
    "NLd": "Ứng dụng công nghệ thông tin và truyền thông trong học và tự học",
    "NLe": "Hợp tác trong môi trường số",
}
TIERS = (
    ("Cơ bản", ("Nhớ", "Hiểu"), ("Đọc hiểu code", "Lập bảng trace", "Điền khuyết 1–2 dòng"), "Có ví dụ mẫu, gợi ý từng bước và bảng trace."),
    ("Đạt chuẩn", ("Vận dụng",), ("Tự viết chương trình theo đặc tả", "Kiểm tra kết quả với ví dụ"), "Có đặc tả đầu vào/đầu ra và một ví dụ."),
    ("Nâng cao", ("Phân tích", "Sáng tạo"), ("Phân tích lỗi logic", "Xử lý trường hợp biên", "Thiết kế lời giải trong phạm vi đã học"), "Giảm gợi ý, yêu cầu giải thích và tự thiết kế test."),
)


def _get(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, Mapping) else getattr(obj, name, default)


def target_minutes_for(scope_mode):
    if scope_mode not in SCOPE_MINUTES:
        raise PedagogyValidationError("scope_mode không hợp lệ")
    return SCOPE_MINUTES[scope_mode]


def normalize_ratios(request):
    """Compatibility name: validate, never silently normalize invalid values."""
    validate_lesson_request(request)
    return [float(_get(request, k)) for k in ("basic_ratio", "standard_ratio", "advanced_ratio")]


def allocate_minutes(request):
    basic, _, advanced = normalize_ratios(request)
    mode = _get(request, "scope_mode")
    minutes = list(BASE_MINUTES[mode])
    shift = 3 if mode == "90m_full" else 2
    # Preserve practice focus in practice mode; no negative durations.
    if basic >= .45:
        minutes[3] -= shift
        minutes[2 if mode == "45m_practice" else 1] += shift
    elif advanced >= .35:
        minutes[1] -= shift
        minutes[3] += shift
    validate_activity_minutes(minutes, _get(request, "target_minutes"))
    return minutes


def _curriculum(bai, taxonomy_path):
    return read_yccd(bai, taxonomy_path)


def _lab_notes(text):
    if not isinstance(text, str):
        raise PedagogyValidationError("lab_constraints phải là văn bản")
    t = unicodedata.normalize("NFC", text).casefold()
    notes = []
    if re.search(r"(?:không có|không đủ|thiếu)\s+(?:phòng máy|máy tính)", t):
        notes.append("Dùng phiếu trace, mô phỏng trên giấy; bố trí thực hành bù khi có thiết bị.")
    if re.search(r"[2-9]\s*(?:hs|học sinh)\s*(?:/|trên|mỗi)\s*(?:1\s*)?máy", t):
        notes.append("Chia nhóm dùng chung máy; luân phiên vai gõ code và kiểm tra, đánh giá từng học sinh.")
    if re.search(r"(?:không có|không dùng|thiếu)\s+máy chiếu", t):
        notes.append("In code mẫu hoặc trình bày trên bảng thay máy chiếu.")
    if re.search(r"(?:không có|không dùng|mất)\s+(?:internet|mạng)", t):
        notes.append("Chuẩn bị Python IDE và học liệu ngoại tuyến.")
    if text.strip() and not notes:
        notes.append("Giáo viên xác nhận phương án tổ chức theo điều kiện phòng máy đã nhập.")
    return notes


def build_profile(request, taxonomy_path=DEFAULT_TAXONOMY):
    ratios = normalize_ratios(request)
    minutes = allocate_minutes(request)
    bai = _get(request, "target_bai")
    items, status = _curriculum(bai, taxonomy_path)
    codes = sorted({c for item in items for c in item["competency_codes"]})
    tiers = []
    for (tier, bloom, tasks, support), ratio in zip(TIERS, ratios):
        tiers.append({"tier": tier, "ratio": ratio, "bloom": list(bloom),
                      "competencies": codes.copy(), "competency_text": [f"{c}: {CS_COMPETENCIES[c]}" for c in codes],
                      "task_types": list(tasks), "support": support,
                      "yccd_ids": [i["yccd_id"] for i in items], "active_in_class": ratio > 0})
    emphasis = []
    if ratios[0] >= .45:
        emphasis.append("Tăng làm mẫu, bảng trace và hỗ trợ từng bước.")
    if ratios[2] >= .35:
        emphasis.append("Bổ sung nhiệm vụ phân tích và trường hợp biên trong phạm vi bài học.")
    if not emphasis:
        emphasis.append("Cân bằng làm mẫu và luyện tập độc lập theo ba nhóm.")
    lab = _get(request, "lab_constraints", "")
    return {"target_bai": bai, "scope_mode": _get(request, "scope_mode"),
            "target_minutes": _get(request, "target_minutes"),
            "activities": [{"activity": a, "minutes": m} for a, m in zip(ACTIVITIES, minutes)],
            "tiers": tiers, "emphasis": " ".join(emphasis), "lab_notes": _lab_notes(lab),
            "lab_constraints": lab, "curriculum_status": status, "yccd_items": items,
            "general_competencies": ["Tự chủ và tự học", "Giao tiếp và hợp tác", "Giải quyết vấn đề và sáng tạo"],
            "qualities": ["Chăm chỉ", "Trách nhiệm", "Trung thực"]}


class ClassProfiler:
    def __init__(self, taxonomy_path=DEFAULT_TAXONOMY):
        self.taxonomy_path = taxonomy_path

    def __call__(self, state):
        profile = build_profile(_get(state, "request"), self.taxonomy_path)
        return {"bloom_matrix_json": json.dumps(profile, ensure_ascii=False, allow_nan=False)}
