"""Các phép kiểm tra sư phạm TẤT ĐỊNH dùng chung cho Critic (trong vòng lặp) và Benchmark (đánh giá).

* `check_duration`      – tổng phút 4 hoạt động khớp 45/90.
* `check_citations`     – trích dẫn không vượt bài, khớp chunk RAG hoặc khớp dải trang SGK của bài.
* `check_differentiation_structure` – đủ 3 phiếu Cơ bản/Đạt chuẩn/Nâng cao, mức Bloom tăng dần đúng bậc.
* `load_yccd`           – đọc `data/yccd_taxonomy.json` (TV2+TV3) theo bài.
* `scope_summary`       – mô tả phạm vi cú pháp Bài k bằng lời (đưa vào prompt LLM Critic).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from src.tools.ast_guard import ROOT, SyllabusRules, _get, canonical_tier, strip_accents, worksheet_label

YCCD_PATH = ROOT / "data" / "yccd_taxonomy.json"

# ----------------------------------------------------------------------------
# Thời lượng
# ----------------------------------------------------------------------------


@dataclass
class DurationCheck:
    total: int
    target: int
    ok: bool
    issues: List[str] = field(default_factory=list)


def check_duration(plan, target_minutes: int) -> DurationCheck:
    activities = list(_get(plan, "activities", []) or [])
    minutes = [int(_get(a, "duration_minutes", 0) or 0) for a in activities]
    total = sum(minutes)
    issues = []
    if total != target_minutes:
        issues.append(f"Tổng thời lượng {total} phút ≠ {target_minutes} phút; cân đối lại phút từng hoạt động ({minutes}).")
    if len(activities) != 4:
        issues.append(f"CV 5512 yêu cầu 4 hoạt động (Khởi động – Hình thành kiến thức – Luyện tập – Vận dụng), hiện có {len(activities)}.")
    for a, m in zip(activities, minutes):
        if m <= 0:
            issues.append(f"Hoạt động '{_get(a, 'activity_name', '?')}' có thời lượng {m} phút không hợp lệ.")
    missing_steps = []
    for a in activities:
        empty = [k for k in ("step1_transfer", "step2_execute", "step3_report", "step4_conclude") if not str(_get(a, k, "") or "").strip()]
        if empty:
            missing_steps.append(f"'{_get(a, 'activity_name', '?')}' thiếu {', '.join(empty)}")
    if missing_steps:
        issues.append("Thiếu bước Tổ chức thực hiện CV 5512: " + "; ".join(missing_steps) + ".")
    return DurationCheck(total=total, target=target_minutes, ok=(total == target_minutes and len(activities) > 0), issues=issues)


# ----------------------------------------------------------------------------
# Trích dẫn
# ----------------------------------------------------------------------------


@dataclass
class CitationCheck:
    total: int
    valid: int
    leaks: int  # số trích dẫn tới bài > target_bai
    issues: List[str] = field(default_factory=list)

    @property
    def precision(self) -> float:
        return self.valid / self.total if self.total else 0.0


def _doc_key(name: str) -> str:
    return " ".join(strip_accents(name or "").lower().split())


def _is_sgk(name: str) -> bool:
    k = _doc_key(name)
    return "sgk" in k or "sach giao khoa" in k


def iter_citations(plan) -> Iterable:
    for a in _get(plan, "activities", []) or []:
        for c in _get(a, "citations", []) or []:
            yield _get(a, "activity_name", ""), c


def check_citations(plan, target_bai: int, retrieved_chunks: Sequence = (), rules: Optional[SyllabusRules] = None) -> CitationCheck:
    rules = rules or SyllabusRules.load()
    retrieved = {
        (_doc_key(_get(c, "doc_name", "")), int(_get(c, "bai_so", 0) or 0), int(_get(c, "page_number", 0) or 0))
        for c in retrieved_chunks or []
    }
    retrieved_pages = {(d, p) for d, _, p in retrieved}
    total = valid = leaks = 0
    issues: List[str] = []
    for act, c in iter_citations(plan):
        total += 1
        doc = _get(c, "doc_name", "") or ""
        bai = int(_get(c, "bai_so", 0) or 0)
        page = int(_get(c, "page_number", 0) or 0)
        where = f"'{act}' → {doc}, Bài {bai}, tr.{page}"
        if bai > target_bai:
            leaks += 1
            issues.append(f"Trích dẫn {where} vượt phạm vi (Bài {bai} > Bài {target_bai}).")
            continue
        key = (_doc_key(doc), bai, page)
        if key in retrieved or (_doc_key(doc), page) in retrieved_pages:
            valid += 1
            continue
        pages = rules.sgk_pages(bai)
        if _is_sgk(doc) and pages and pages[0] <= page <= pages[1]:
            valid += 1
            continue
        if _is_sgk(doc) and pages:
            issues.append(f"Trích dẫn {where} sai trang: Bài {bai} SGK nằm ở tr.{pages[0]}–{pages[1]}.")
        else:
            issues.append(f"Trích dẫn {where} không khớp tài liệu truy hồi nào (nghi bịa nguồn).")
    if total == 0:
        issues.append("Giáo án không có trích dẫn nguồn (SourceCitation) nào.")
    return CitationCheck(total=total, valid=valid, leaks=leaks, issues=issues)


# ----------------------------------------------------------------------------
# Phân hoá Bloom
# ----------------------------------------------------------------------------

_BLOOM_KEYS = [
    (5, ("sang tao", "create", "creat")),
    (4, ("danh gia", "evaluat")),
    (3, ("phan tich", "analy")),
    (2, ("van dung", "apply", "applic")),
    (1, ("hieu", "understand", "comprehen")),
    (0, ("nho", "biet", "remember", "knowledge")),
]
BLOOM_NAMES = ["Nhớ", "Hiểu", "Vận dụng", "Phân tích", "Đánh giá", "Sáng tạo"]
# Bậc Bloom kỳ vọng theo kế hoạch: Cơ bản (Nhớ/Hiểu) – Đạt chuẩn (Vận dụng) – Nâng cao (Phân tích/Sáng tạo)
EXPECTED_BLOOM = {"co ban": (0, 1), "dat chuan": (2, 2), "nang cao": (3, 5)}


def bloom_rank(level: str) -> Optional[int]:
    """Bậc Bloom CAO NHẤT được nhắc tới trong chuỗi (0=Nhớ … 5=Sáng tạo)."""
    text = strip_accents(level or "").lower()
    for rank, keys in _BLOOM_KEYS:
        if any(k in text for k in keys):
            return rank
    return None


@dataclass
class DifferentiationCheck:
    score: float
    tiers_found: List[str]
    issues: List[str] = field(default_factory=list)


def check_differentiation_structure(worksheets: Sequence) -> DifferentiationCheck:
    worksheets = list(worksheets or [])
    found = {}
    issues: List[str] = []
    for i, ws in enumerate(worksheets):
        tier = canonical_tier(_get(ws, "tier", "") or "")
        if tier is None:
            issues.append(f"Phiếu #{i + 1}: tier '{_get(ws, 'tier', '')}' không thuộc Cơ bản/Đạt chuẩn/Nâng cao.")
            continue
        found.setdefault(tier, ws)
    tier_score = len(found) / 3
    for t in EXPECTED_BLOOM:
        if t not in found:
            issues.append(f"Thiếu {worksheet_label(t)}.")
    bloom_ok = 0
    ranks = {}
    for t, ws in found.items():
        lvl = _get(ws, "bloom_level", "") or ""
        r = bloom_rank(lvl)
        ranks[t] = r
        lo, hi = EXPECTED_BLOOM[t]
        if r is None:
            issues.append(f"{worksheet_label(t)}: không xác định được mức Bloom ('{lvl}').")
        elif lo <= r <= hi:
            bloom_ok += 1
        else:
            exp = "/".join(BLOOM_NAMES[lo : hi + 1])
            issues.append(f"{worksheet_label(t)}: mức Bloom '{lvl}' chưa đúng bậc kỳ vọng ({exp}).")
    order = [ranks.get(t) for t in ("co ban", "dat chuan", "nang cao") if ranks.get(t) is not None]
    if any(b <= a for a, b in zip(order, order[1:])):
        issues.append("Mức Bloom 3 phiếu chưa tăng dần Cơ bản < Đạt chuẩn < Nâng cao.")
    bloom_score = bloom_ok / 3
    score = round(0.5 * tier_score + 0.5 * bloom_score, 4)
    return DifferentiationCheck(score=score, tiers_found=sorted(found), issues=issues)


# ----------------------------------------------------------------------------
# YCCĐ & phạm vi
# ----------------------------------------------------------------------------


def load_yccd(bai: int, path: Optional[Path | str] = None) -> List[dict]:
    """Trả list {code, text} của Bài `bai`. Hỗ trợ 3 dạng file:
    1) {"F10.B20.YC1": {"bai_so": 20, "text": "..."}}
    2) [{"code": "...", "bai_so": 20, "text": "..."}]
    3) {"20": [{"code": "...", "text": "..."}]}  hoặc {"lessons": [...]} với key 'yccd'
    """
    p = Path(path) if path is not None else YCCD_PATH
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    out: List[dict] = []

    def norm(item, code=None):
        if isinstance(item, str):
            return {"code": code or "", "text": item}
        return {"code": item.get("code", code or ""), "text": item.get("text") or item.get("description") or item.get("noi_dung", "")}

    if isinstance(data, dict):
        data = {k: v for k, v in data.items() if not k.startswith("_")}
    if isinstance(data, dict) and "lessons" in data:
        data = data["lessons"]
    if isinstance(data, list):
        for it in data:
            if int(it.get("bai_so", it.get("bai", -1))) == bai:
                if "yccd" in it:
                    out.extend(norm(x) for x in it["yccd"])
                else:
                    out.append(norm(it))
    elif isinstance(data, dict):
        if str(bai) in data and isinstance(data[str(bai)], list):
            out.extend(norm(x) for x in data[str(bai)])
        else:
            for code, it in data.items():
                if isinstance(it, dict) and int(it.get("bai_so", it.get("bai", -1))) == bai:
                    out.append(norm(it, code))
                elif f".B{bai}." in code:
                    out.append(norm(it, code))
    return out


def scope_summary(target_bai: int, rules: Optional[SyllabusRules] = None) -> str:
    rules = rules or SyllabusRules.load()
    scope = rules.scope(target_bai)
    structural = sorted(n for n in scope.ast_nodes if n not in rules.always_nodes)
    names = [f"{n}" + (f" ({rules.display(n)})" if rules.display(n) else "") for n in structural]
    lines = [
        f"Phạm vi cú pháp tích luỹ đến Bài {scope.target_bai} (SGK Tin học 10 KNTT):",
        "- Cấu trúc được phép: " + ", ".join(names),
        "- Hàm dựng sẵn được phép: " + ", ".join(sorted(scope.builtins)),
        "- Phương thức được phép: " + (", ".join("." + m + "()" for m in sorted(scope.methods)) or "(chưa có)"),
        "- Mọi cấu trúc/hàm/phương thức khác đều VƯỢT CHƯƠNG TRÌNH; cấm mọi import.",
    ]
    return "\n".join(lines)
