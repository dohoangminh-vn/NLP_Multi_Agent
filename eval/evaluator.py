"""Bộ chấm THỐNG NHẤT cho cả 4 cấu hình (B1–B4).

Nguyên tắc công bằng: dù cấu hình có dùng AST/Sandbox trong vòng lặp hay không, KHI ĐÁNH GIÁ
mọi giáo án đều được đo bằng cùng AST Guard + Sandbox + kiểm tra tất định + cùng LLM-as-a-Judge.

Định nghĩa metric (dùng nguyên văn trong báo cáo):
* out_of_scope_rate   = số phiếu có ≥1 lỗi AST (vượt phạm vi Bài k) / tổng số phiếu đã sinh.
* code_pass_rate      = số phiếu PASS toàn bộ test_cases / (3 × số ca)  – phiếu thiếu/không hợp lệ tính là trượt.
* test_case_pass_rate = số test PASS / tổng số test.
* citation_precision  = trích dẫn hợp lệ / tổng trích dẫn (min với điểm LLM xác nhận nếu có).
* faithfulness, yccd_coverage, differentiation_score: thang 0–1 (LLM-as-a-Judge + phần tất định).
* recall_at_5, mrr    = theo trang SGK đúng của Bài k trong top-k chunk truy hồi (None với B1).
* critic_miss_rate    = trong các ca Critic tuyên bố ĐẠT, tỉ lệ ca vẫn còn lỗi AST/Sandbox (đo độ "sót" của Critic).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from eval.baselines import SystemOutput
from eval.metrics import mean, percentile, reciprocal_rank, recall_at_k, safe_div
from src.agents.critic import CriticConfig, SYSTEM_PROMPT, build_llm_prompt, parse_llm_scores
from src.schemas import LessonRequest, SourceCitation
from src.tools.ast_guard import SyllabusGuard, SyllabusRules
from src.tools.code_sandbox import verify_worksheets
from src.tools.pedagogy_checks import check_citations, check_differentiation_structure, check_duration, load_yccd
from src.utils.llm import LLMClient

EXPECTED_WORKSHEETS = 3


@dataclass
class CaseEvaluation:
    case_id: str
    config: str
    target_bai: int
    scope_mode: str
    json_valid: bool
    error: str = ""
    # code
    n_worksheets: int = 0
    n_oos_worksheets: int = 0
    oos_violation_names: List[str] = field(default_factory=list)
    oos_messages: List[str] = field(default_factory=list)
    soft_warnings: int = 0
    n_tests: int = 0
    n_tests_passed: int = 0
    n_worksheets_code_pass: int = 0
    sandbox_statuses: List[str] = field(default_factory=list)
    sandbox_messages: List[str] = field(default_factory=list)
    # sư phạm
    total_minutes: int = 0
    duration_ok: bool = False
    citations_total: int = 0
    citation_leaks: int = 0
    citation_precision: float = 0.0
    differentiation_structure: float = 0.0
    faithfulness: float = 0.0
    yccd_coverage: float = 0.0
    differentiation_score: float = 0.0
    judge_ok: bool = False
    judge_likert: Dict[str, int] = field(default_factory=dict)
    # retrieval
    recall_at_5: Optional[float] = None
    mrr: Optional[float] = None
    # chi phí
    latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    cost_usd: float = 0.0
    iterations: int = 1
    critic_passed: Optional[bool] = None

    @property
    def has_code_defect(self) -> bool:
        return self.n_oos_worksheets > 0 or self.n_worksheets_code_pass < EXPECTED_WORKSHEETS

    def to_dict(self) -> dict:
        return asdict(self)


class Evaluator:
    def __init__(self, judge_llm: Optional[LLMClient] = None, rules: Optional[SyllabusRules] = None,
                 yccd_loader: Callable[[int], List[dict]] = load_yccd, sandbox_timeout: float = 2.0):
        self.judge_llm = judge_llm
        self.rules = rules or SyllabusRules.load()
        self.guard = SyllabusGuard(self.rules)
        self.yccd_loader = yccd_loader
        self.sandbox_timeout = sandbox_timeout

    def gold_pages(self, case: dict) -> List[int]:
        gold = case.get("gold", {}).get("sgk_pages")
        if gold:
            return list(gold)
        pages = self.rules.sgk_pages(case["request"]["target_bai"]) or []
        return list(range(pages[0], pages[1] + 1)) if pages else []

    def evaluate(self, case: dict, config: str, out: SystemOutput, reference_chunks: Sequence[SourceCitation] = ()) -> CaseEvaluation:
        req = LessonRequest.model_validate(case["request"])
        ev = CaseEvaluation(case_id=case["case_id"], config=config, target_bai=req.target_bai, scope_mode=req.scope_mode,
                            json_valid=out.plan is not None, error=out.error, iterations=out.iterations, critic_passed=out.critic_passed)

        # ---- retrieval (theo trang SGK của bài mục tiêu) ----
        if out.retrieved_chunks:
            gold = set(self.gold_pages(case))
            ranked = [c.page_number if c.bai_so == req.target_bai else None for c in out.retrieved_chunks]
            ev.recall_at_5 = recall_at_k(ranked, gold, 5)
            ev.mrr = reciprocal_rank(ranked, gold)

        plan = out.plan
        if plan is None:
            return ev

        # ---- AST ----
        ws = list(plan.worksheets)
        ev.n_worksheets = len(ws)
        for g in self.guard.check_worksheets(ws, req.target_bai):
            if g.errors:
                ev.n_oos_worksheets += 1
            ev.oos_violation_names.extend(f"{v.tier}:{v.name}" for v in g.errors)
            ev.oos_messages.extend(g.messages())
            ev.soft_warnings += len(g.warnings)

        # ---- Sandbox ----
        for r in verify_worksheets(ws, timeout=self.sandbox_timeout):
            ev.n_tests += r.total
            ev.n_tests_passed += r.n_passed
            ev.n_worksheets_code_pass += int(r.passed)
            ev.sandbox_statuses.extend(c.status for c in r.cases)
            ev.sandbox_messages.extend(r.failure_messages())
        ev.n_worksheets_code_pass = min(ev.n_worksheets_code_pass, EXPECTED_WORKSHEETS)

        # ---- Kiểm tra tất định ----
        d = check_duration(plan, req.target_minutes)
        ev.total_minutes, ev.duration_ok = d.total, d.ok
        context = list(reference_chunks) or list(out.retrieved_chunks)
        cit = check_citations(plan, req.target_bai, context, self.rules)
        ev.citations_total, ev.citation_leaks = cit.total, cit.leaks
        diff = check_differentiation_structure(ws)
        ev.differentiation_structure = diff.score
        citation = cit.precision
        diff_score = diff.score

        # ---- LLM-as-a-Judge ----
        if self.judge_llm is not None:
            yccd = self.yccd_loader(req.target_bai) if self.yccd_loader else []
            prompt = build_llm_prompt(req, plan, context, yccd, CriticConfig())
            try:
                s = parse_llm_scores(self.judge_llm.complete(SYSTEM_PROMPT, prompt).text)
            except Exception as exc:
                s = None
                ev.error = (ev.error + f" | judge lỗi: {exc}").strip(" |")
            if s is not None and s.ok:
                ev.judge_ok = True
                ev.judge_likert = dict(s.likert)
                ev.faithfulness = s.faithfulness or 0.0
                if s.citation_support is not None:
                    citation = min(citation, s.citation_support)
                codes = {y["code"] for y in yccd if y.get("code")}
                if codes:
                    ev.yccd_coverage = len(codes.intersection(s.yccd_covered)) / len(codes)
                else:
                    ev.yccd_coverage = s.yccd_score or 0.0
                if s.differentiation is not None:
                    diff_score = (diff.score + s.differentiation) / 2
        ev.citation_precision = round(citation, 4)
        ev.differentiation_score = round(diff_score, 4)
        return ev


def summarize(evals: Sequence[CaseEvaluation], price_in: float = 0.0, price_out: float = 0.0) -> Dict[str, Optional[float]]:
    """Gộp các ca của 1 cấu hình → bảng số liệu (làm tròn 4 chữ số – NGUỒN SỰ THẬT cho báo cáo)."""
    n = len(evals)
    if n == 0:
        return {}
    total_ws = sum(e.n_worksheets for e in evals)
    critic_claimed = [e for e in evals if e.critic_passed]
    judged = [e for e in evals if e.judge_ok]
    out = {
        "n_cases": n,
        "json_valid_rate": safe_div(sum(e.json_valid for e in evals), n),
        "out_of_scope_rate": safe_div(sum(e.n_oos_worksheets for e in evals), total_ws),
        "code_pass_rate": safe_div(sum(e.n_worksheets_code_pass for e in evals), EXPECTED_WORKSHEETS * n),
        "test_case_pass_rate": safe_div(sum(e.n_tests_passed for e in evals), sum(e.n_tests for e in evals)),
        "duration_match_rate": safe_div(sum(e.duration_ok for e in evals), n),
        "citation_precision": mean(e.citation_precision for e in evals),
        "citation_leak_rate": safe_div(sum(e.citation_leaks for e in evals), sum(e.citations_total for e in evals)),
        "faithfulness": mean(e.faithfulness for e in judged) if judged else None,
        "yccd_coverage": mean(e.yccd_coverage for e in judged) if judged else None,
        "differentiation_score": mean(e.differentiation_score for e in evals),
        "recall_at_5": mean(e.recall_at_5 for e in evals),
        "mrr": mean(e.mrr for e in evals),
        "latency_s_mean": mean(e.latency_s for e in evals),
        "latency_s_p95": percentile([e.latency_s for e in evals], 0.95),
        "tokens_per_case": mean(e.input_tokens + e.output_tokens for e in evals),
        "total_tokens": float(sum(e.input_tokens + e.output_tokens for e in evals)),
        "token_cost_usd": sum(e.input_tokens * price_in + e.output_tokens * price_out for e in evals) / 1_000_000,
        "avg_iterations": mean(e.iterations for e in evals),
        "critic_claimed_pass_rate": safe_div(len(critic_claimed), n) if any(e.critic_passed is not None for e in evals) else None,
        "critic_miss_rate": safe_div(sum(e.has_code_defect for e in critic_claimed), len(critic_claimed)) if critic_claimed else None,
        "judge_ok_rate": safe_div(len(judged), n),
    }
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()}
