"""Pedagogy & Grounding Critic – node kiểm định tổng hợp (TV4).

Kết hợp:
  (1) AST Syllabus Guard 3 tầng trên solution_code         → `ast_violations`
  (2) Code Sandbox chạy test_cases                         → `sandbox_failures`
  (3) Kiểm tra tất định: tổng phút 45/90, trích dẫn, cấu trúc 3 phiếu Bloom
  (4) 1 lượt LLM chấm 5 trục: Faithfulness, Citation, YCCĐ Coverage, Thời lượng, Phân hoá Bloom

Cấu hình Ablation:
  * B4 (Full)   : CriticConfig()                                   – đủ (1)(2)(3)(4)
  * B3 (LLM-only): CriticConfig(use_ast=False, use_sandbox=False)   – LLM tự soát code vượt cấp/lỗi runtime

Dùng như node LangGraph:
    critic = CriticAgent(llm)
    graph.add_node("critic", critic)      # nhận state (dict | AgentState) → {"critic_report", "iteration"}
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Callable, Iterable, List, Optional, Sequence

from src.schemas import CriticReport, LessonPlan5512, LessonRequest, SourceCitation
from src.rag.ingest import load_yccd
from src.tools.ast_guard import (
    GuardResult,
    SyllabusGuard,
    SyllabusRules,
    _get,
    canonical_tier,
    scope_summary,
    strip_accents,
    worksheet_label,
)
from src.tools.code_sandbox import DEFAULT_TIMEOUT_S, WorksheetSandboxResult, verify_worksheets
from src.utils.llm import LLMClient, extract_json


# ============================================================================
# Kiểm tra sư phạm TẤT ĐỊNH (dùng chung cho Critic trong vòng lặp và Benchmark)
#   check_duration · check_citations · check_differentiation_structure
# ============================================================================

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


@dataclass
class CriticConfig:
    use_ast: bool = True
    use_sandbox: bool = True
    use_llm: bool = True
    strict_ast: bool = False  # True: elif / += / pass cũng là lỗi
    sandbox_timeout: float = DEFAULT_TIMEOUT_S
    min_faithfulness: float = 0.8
    min_citation_precision: float = 0.8
    min_yccd_coverage: float = 0.8
    min_differentiation: float = 0.7
    max_context_chars: int = 12000
    increment_iteration: bool = True

    @classmethod
    def full(cls, **kw) -> "CriticConfig":
        return cls(**kw)

    @classmethod
    def llm_only(cls, **kw) -> "CriticConfig":
        return cls(use_ast=False, use_sandbox=False, use_llm=True, **kw)


@dataclass
class LLMScores:
    ok: bool = False  # parse được JSON hay không
    faithfulness: Optional[float] = None
    citation_support: Optional[float] = None
    yccd_covered: List[str] = field(default_factory=list)
    yccd_score: Optional[float] = None
    timing: Optional[float] = None
    differentiation: Optional[float] = None
    issues: List[str] = field(default_factory=list)
    code_scope_issues: List[str] = field(default_factory=list)
    code_runtime_issues: List[str] = field(default_factory=list)
    likert: dict = field(default_factory=dict)  # điểm gốc 1–5 từng trục (phục vụ Human Eval)
    raw: str = ""


@dataclass
class CriticResult:
    report: CriticReport
    ast_results: List[GuardResult] = field(default_factory=list)
    sandbox_results: List[WorksheetSandboxResult] = field(default_factory=list)
    duration: Optional[DurationCheck] = None
    citations: Optional[CitationCheck] = None
    differentiation: Optional[DifferentiationCheck] = None
    llm: Optional[LLMScores] = None
    warnings: List[str] = field(default_factory=list)
    gate_failures: List[str] = field(default_factory=list)

    def to_trace_event(self) -> dict:
        """Sự kiện cho Live Trace (TV5): thẻ đỏ khi có lỗi, thẻ xanh khi đạt."""
        r = self.report
        return {
            "node": "critic",
            "status": "green" if r.is_passed else "red",
            "ast_violations": r.ast_violations,
            "sandbox_failures": r.sandbox_failures,
            "ast_pass": not r.ast_violations,
            "sandbox_pass": not r.sandbox_failures,
            "scores": {
                "faithfulness": r.faithfulness_score,
                "citation_precision": r.citation_precision,
                "yccd_coverage": r.yccd_coverage,
                "differentiation": r.differentiation_score,
                "total_minutes": r.total_minutes_checked,
            },
            "gate_failures": self.gate_failures,
            "warnings": self.warnings,
        }

    def to_dict(self) -> dict:
        return {
            "report": self.report.model_dump(),
            "ast_results": [
                {"worksheet": g.worksheet, "passed": g.passed, "violations": [v.to_dict() for v in g.violations]} for g in self.ast_results
            ],
            "sandbox_results": [s.to_dict() for s in self.sandbox_results],
            "duration": asdict(self.duration) if self.duration else None,
            "citations": (asdict(self.citations) | {"precision": self.citations.precision}) if self.citations else None,
            "differentiation": asdict(self.differentiation) if self.differentiation else None,
            "llm": asdict(self.llm) if self.llm else None,
            "warnings": self.warnings,
            "gate_failures": self.gate_failures,
        }


SYSTEM_PROMPT = """Bạn là chuyên gia thẩm định giáo án Tin học THPT theo Công văn 5512/BGDĐT-GDTrH \
và SGK Tin học 10 – Kết nối tri thức. Nhiệm vụ: chấm giáo án nháp một cách nghiêm khắc, chỉ dựa trên \
TÀI LIỆU NGUỒN được cung cấp. Chỉ trả về MỘT object JSON hợp lệ, không giải thích thêm."""

_RUBRIC = """Thang điểm mỗi trục: số nguyên 1–5 (1 = rất kém, 3 = đạt một phần, 5 = hoàn toàn đạt).
- faithfulness: nội dung kiến thức/ví dụ/bài tập có bám TÀI LIỆU NGUỒN, không bịa kiến thức ngoài nguồn hay vượt bài.
- citation_support: các trích dẫn (doc, bài, trang, đoạn trích) có thật sự hỗ trợ nội dung hoạt động gắn với nó.
- yccd: giáo án bao phủ các Yêu cầu cần đạt (YCCĐ) liệt kê; trả về danh sách mã đã bao phủ.
- timing: phân bổ phút hợp lý cho 4 hoạt động, khớp {target} phút ({scope_mode}).
- differentiation: 3 phiếu Cơ bản (Nhớ/Hiểu) – Đạt chuẩn (Vận dụng) – Nâng cao (Phân tích/Sáng tạo) khác biệt rõ về mức nhận thức."""

_CODE_TASK = """
NGOÀI RA (không có công cụ kiểm định tự động) bạn phải TỰ soát code lời giải của từng phiếu:
- scope_violations: mọi cấu trúc/hàm/phương thức vượt phạm vi Bài {bai} (ghi rõ phiếu + dòng).
- runtime_issues: code chạy sai/lỗi với test case (ghi rõ phiếu + test)."""

_OUTPUT = """Định dạng JSON bắt buộc:
{{
  "faithfulness": {{"score": 1-5, "unsupported_claims": ["..."]}},
  "citation_support": {{"score": 1-5, "bad_citations": ["..."]}},
  "yccd": {{"score": 1-5, "covered": ["mã YCCĐ"], "missing": ["mã YCCĐ"]}},
  "timing": {{"score": 1-5, "issues": ["..."]}},
  "differentiation": {{"score": 1-5, "issues": ["..."]}},{code_field}
  "revision_suggestions": ["chỉ dẫn sửa cụ thể, ngắn gọn"]
}}"""


def _score01(x) -> Optional[float]:
    """1–5 → 0–1. Chấp nhận cả số thực 0–1."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if 0.0 <= v <= 1.0 and not float(v).is_integer():
        return round(v, 4)
    v = max(1.0, min(5.0, v))
    return round((v - 1) / 4, 4)


def _axis(obj: dict, key: str) -> dict:
    v = obj.get(key) or {}
    return v if isinstance(v, dict) else {"score": v}


def parse_llm_scores(text: str) -> LLMScores:
    data = extract_json(text)
    s = LLMScores(raw=text or "")
    if not data:
        s.issues.append("LLM Critic không trả về JSON hợp lệ.")
        return s
    s.ok = True
    f = _axis(data, "faithfulness")
    c = _axis(data, "citation_support")
    y = _axis(data, "yccd")
    t = _axis(data, "timing")
    d = _axis(data, "differentiation")
    s.faithfulness = _score01(f.get("score"))
    s.citation_support = _score01(c.get("score"))
    s.yccd_score = _score01(y.get("score"))
    s.yccd_covered = [str(x) for x in (y.get("covered") or [])]
    s.timing = _score01(t.get("score"))
    s.differentiation = _score01(d.get("score"))
    for key, axis in (("faithfulness", f), ("citation", c), ("yccd", y), ("timing", t), ("differentiation", d)):
        try:
            s.likert[key] = int(round(max(1.0, min(5.0, float(axis.get("score"))))))
        except (TypeError, ValueError):
            pass
    for label, lst in (
        ("Bịa/ngoài nguồn", f.get("unsupported_claims")),
        ("Trích dẫn", c.get("bad_citations")),
        ("Thiếu YCCĐ", y.get("missing")),
        ("Thời lượng", t.get("issues")),
        ("Phân hoá", d.get("issues")),
    ):
        for item in lst or []:
            s.issues.append(f"{label}: {item}")
    for item in data.get("revision_suggestions") or []:
        s.issues.append(str(item))
    cr = data.get("code_review") or {}
    if isinstance(cr, dict):
        s.code_scope_issues = [str(x) for x in cr.get("scope_violations") or []]
        s.code_runtime_issues = [str(x) for x in cr.get("runtime_issues") or []]
    return s


def _format_chunks(chunks: Sequence, limit: int) -> str:
    parts, used = [], 0
    for i, c in enumerate(chunks or []):
        line = (
            f"[{i + 1}] {_get(c, 'doc_name', '')} – Bài {_get(c, 'bai_so', '')} – "
            f"{_get(c, 'section_title', '')} – tr.{_get(c, 'page_number', '')}: {_get(c, 'quoted_text', '')}"
        )
        if used + len(line) > limit:
            parts.append(f"... (bỏ bớt {len(chunks) - i} chunk do giới hạn độ dài)")
            break
        parts.append(line)
        used += len(line)
    return "\n".join(parts) or "(không có tài liệu truy hồi)"


def _plan_for_prompt(plan: LessonPlan5512, include_code: bool) -> str:
    d = plan.model_dump()
    for ws in d.get("worksheets", []):
        if not include_code:
            ws.pop("solution_code", None)
            ws.pop("test_cases", None)
    return json.dumps(d, ensure_ascii=False, indent=1)


def build_llm_prompt(request: LessonRequest, plan: LessonPlan5512, chunks: Sequence, yccd: List[dict], config: CriticConfig) -> str:
    check_code = not (config.use_ast and config.use_sandbox)
    yccd_text = "\n".join(f"- {y['code']}: {y['text']}" for y in yccd) or "(chưa có taxonomy – tự suy từ tài liệu nguồn)"
    parts = [
        f"YÊU CẦU: Bài {request.target_bai} – {request.topic_name}; chế độ {request.scope_mode}, {request.target_minutes} phút; "
        f"tỉ lệ lớp Cơ bản/Đạt chuẩn/Nâng cao = {request.basic_ratio:.0%}/{request.standard_ratio:.0%}/{request.advanced_ratio:.0%}.",
        "\nTÀI LIỆU NGUỒN (RAG):\n" + _format_chunks(chunks, config.max_context_chars),
        "\nYCCĐ CỦA BÀI:\n" + yccd_text,
        "\n" + scope_summary(request.target_bai),
        "\nGIÁO ÁN NHÁP (JSON):\n" + _plan_for_prompt(plan, include_code=check_code),
        "\n" + _RUBRIC.format(target=request.target_minutes, scope_mode=request.scope_mode),
    ]
    if check_code:
        parts.append(_CODE_TASK.format(bai=request.target_bai))
    code_field = '\n  "code_review": {"scope_violations": ["..."], "runtime_issues": ["..."]},' if check_code else ""
    parts.append("\n" + _OUTPUT.format(code_field=code_field))
    return "\n".join(parts)


class CriticAgent:
    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        config: Optional[CriticConfig] = None,
        guard: Optional[SyllabusGuard] = None,
        yccd_loader: Callable[[int], List[dict]] = load_yccd,
    ):
        self.llm = llm
        self.config = config or CriticConfig()
        self.guard = guard or SyllabusGuard.from_file(strict=self.config.strict_ast)
        self.yccd_loader = yccd_loader
        self.history: List[CriticResult] = []

    # ---------------- LangGraph node ----------------

    def __call__(self, state) -> dict:
        request = _coerce(_get(state, "request"), LessonRequest)
        plan = _get(state, "draft_plan")
        chunks = [_coerce(c, SourceCitation) for c in (_get(state, "retrieved_chunks", []) or [])]
        iteration = int(_get(state, "iteration", 0) or 0)
        if plan is None:
            report = CriticReport(is_passed=False, revision_instructions="Chưa có draft_plan để kiểm định.")
        else:
            report = self.review(request, _coerce(plan, LessonPlan5512), chunks).report
        update = {"critic_report": report}
        if self.config.increment_iteration:
            update["iteration"] = iteration + 1
        return update

    # ---------------- Kiểm định ----------------

    def review(self, request: LessonRequest, plan: LessonPlan5512, retrieved_chunks: Sequence = ()) -> CriticResult:
        cfg = self.config
        bai = request.target_bai
        result = CriticResult(report=CriticReport())

        ast_msgs: List[str] = []
        if cfg.use_ast:
            result.ast_results = self.guard.check_worksheets(plan.worksheets, bai)
            for g in result.ast_results:
                ast_msgs.extend(g.messages())
                result.warnings.extend(v.message for v in g.warnings)

        sbx_msgs: List[str] = []
        if cfg.use_sandbox:
            result.sandbox_results = verify_worksheets(plan.worksheets, timeout=cfg.sandbox_timeout)
            for s in result.sandbox_results:
                sbx_msgs.extend(s.failure_messages())
                result.warnings.extend(s.warnings)

        result.duration = check_duration(plan, request.target_minutes)
        result.citations = check_citations(plan, bai, retrieved_chunks, self.guard.rules)
        result.differentiation = check_differentiation_structure(plan.worksheets)
        yccd = self.yccd_loader(bai) if self.yccd_loader else []

        llm_scores: Optional[LLMScores] = None
        if cfg.use_llm and self.llm is not None:
            prompt = build_llm_prompt(request, plan, retrieved_chunks, yccd, cfg)
            try:
                llm_scores = parse_llm_scores(self.llm.complete(SYSTEM_PROMPT, prompt).text)
            except Exception as exc:  # lỗi mạng/rate-limit không được làm sập đồ thị
                llm_scores = LLMScores(ok=False, issues=[f"Lỗi gọi LLM Critic: {exc}"])
            result.llm = llm_scores
            if not cfg.use_ast:
                ast_msgs.extend(f"[LLM] {m}" for m in llm_scores.code_scope_issues)
            if not cfg.use_sandbox:
                sbx_msgs.extend(f"[LLM] {m}" for m in llm_scores.code_runtime_issues)

        # ---- Hợp nhất điểm ----
        det_cit = result.citations.precision
        llm_ok = bool(llm_scores and llm_scores.ok)
        faith = llm_scores.faithfulness if llm_ok and llm_scores.faithfulness is not None else 0.0
        if llm_ok and llm_scores.citation_support is not None:
            citation = min(det_cit, llm_scores.citation_support)
        else:
            citation = det_cit
        if yccd and llm_ok:
            codes = {y["code"] for y in yccd if y.get("code")}
            covered = codes.intersection(llm_scores.yccd_covered)
            coverage = len(covered) / len(codes) if codes else (llm_scores.yccd_score or 0.0)
        else:
            coverage = llm_scores.yccd_score if llm_ok and llm_scores.yccd_score is not None else 0.0
        diff_struct = result.differentiation.score
        if llm_ok and llm_scores.differentiation is not None:
            diff = round((diff_struct + llm_scores.differentiation) / 2, 4)
        else:
            diff = diff_struct

        gates: List[str] = []
        if ast_msgs:
            gates.append(f"AST: {len(ast_msgs)} vi phạm phạm vi cú pháp")
        if sbx_msgs:
            gates.append(f"Sandbox: {len(sbx_msgs)} test lỗi")
        if not result.duration.ok:
            gates.append(f"Thời lượng {result.duration.total}/{request.target_minutes} phút")
        if result.citations.leaks:
            gates.append(f"{result.citations.leaks} trích dẫn vượt bài")
        if citation < cfg.min_citation_precision:
            gates.append(f"Citation Precision {citation:.2f} < {cfg.min_citation_precision}")
        if diff < cfg.min_differentiation:
            gates.append(f"Differentiation {diff:.2f} < {cfg.min_differentiation}")
        if cfg.use_llm and self.llm is not None:
            if not llm_ok:
                gates.append("LLM Critic không chấm được")
            else:
                if faith < cfg.min_faithfulness:
                    gates.append(f"Faithfulness {faith:.2f} < {cfg.min_faithfulness}")
                if coverage < cfg.min_yccd_coverage:
                    gates.append(f"YCCĐ Coverage {coverage:.2f} < {cfg.min_yccd_coverage}")
        result.gate_failures = gates

        report = CriticReport(
            is_passed=not gates,
            ast_violations=ast_msgs,
            sandbox_failures=sbx_msgs,
            faithfulness_score=round(faith, 4),
            citation_precision=round(citation, 4),
            yccd_coverage=round(coverage, 4),
            differentiation_score=round(diff, 4),
            total_minutes_checked=result.duration.total,
        )
        report.revision_instructions = "" if report.is_passed else self._revision_text(result, ast_msgs, sbx_msgs, llm_scores)
        result.report = report
        self.history.append(result)
        return result

    # ---------------- Revision Mode ----------------

    def _revision_text(self, result: CriticResult, ast_msgs, sbx_msgs, llm_scores: Optional[LLMScores]) -> str:
        out: List[str] = ["REVISION MODE – chỉ sửa các mục dưới đây, GIỮ NGUYÊN phần đã đạt."]
        hints = {v.message: v.hint for g in result.ast_results for v in g.errors if v.hint}
        if ast_msgs:
            out.append("\n[1] VƯỢT PHẠM VI CÚ PHÁP (bắt buộc sửa solution_code, và starter_code tương ứng):")
            for m in ast_msgs:
                out.append(f"  - {m}" + (f" → Gợi ý: {hints[m]}" if m in hints else ""))
        if sbx_msgs:
            out.append("\n[2] SANDBOX – solution_code chạy sai test_cases (sửa code hoặc expected_stdout cho đúng):")
            out.extend(f"  - {m}" for m in sbx_msgs)
        det = []
        if result.duration and result.duration.issues:
            det.extend(result.duration.issues)
        if result.citations and result.citations.issues:
            det.extend(result.citations.issues)
        if result.differentiation and result.differentiation.issues:
            det.extend(result.differentiation.issues)
        if det:
            out.append("\n[3] CẤU TRÚC 5512 / TRÍCH DẪN / PHÂN HOÁ:")
            out.extend(f"  - {m}" for m in det)
        if llm_scores and llm_scores.issues:
            out.append("\n[4] NHẬN XÉT SƯ PHẠM (LLM Critic):")
            out.extend(f"  - {m}" for m in llm_scores.issues[:15])
        return "\n".join(out)


def _coerce(obj, model):
    if obj is None or isinstance(obj, model):
        return obj
    if isinstance(obj, dict):
        return model.model_validate(obj)
    if hasattr(obj, "model_dump"):
        return model.model_validate(obj.model_dump())
    return obj
