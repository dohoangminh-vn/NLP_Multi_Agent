"""Chạy Ablation 4 cấu hình (B1–B4) + log MLflow.

Ví dụ (chạy chính thức Tuần 3):
    LLM_PROVIDER=google_genai LLM_MODEL=gemini-2.0-flash \
    python -m eval.run_benchmark --configs B1,B2,B3,B4 \
        --retriever src.agents.retriever:build_benchmark_retriever \
        --graph src.graph.workflow:build_graph \
        --price-in 0.10 --price-out 0.40

Chạy thử trước tích hợp (vòng Designer↔Critic mô phỏng):
    python -m eval.run_benchmark --configs B1,B2,B3,B4 --fallback-loop --limit 3 ...

Đầu ra (NGUỒN SỰ THẬT cho báo cáo – verify_report_metrics.py đối soát với file này):
    eval/results/benchmark_results.json   – meta + summary từng cấu hình + từng ca + MLflow run_id
    eval/results/ablation_table.md        – bảng so sánh sinh tự động
    eval/results/plans/<B?>/<case_id>.json – giáo án từng ca (phục vụ Human Eval)
"""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import math
import random
import subprocess
import sys
import time
import traceback
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.baselines import FunctionRetriever, SystemOutput, build_systems, load_object  # noqa: E402
from eval.retrieval_eval import recall_at_k, reciprocal_rank  # noqa: E402
from src.agents.critic import (  # noqa: E402
    SYSTEM_PROMPT,
    CriticConfig,
    build_llm_prompt,
    check_citations,
    check_differentiation_structure,
    check_duration,
    parse_llm_scores,
)
from src.rag.ingest import load_yccd  # noqa: E402
from src.schemas import LessonRequest, SourceCitation  # noqa: E402
from src.tools.ast_guard import SyllabusGuard, SyllabusRules  # noqa: E402
from src.tools.code_sandbox import verify_worksheets  # noqa: E402
from src.utils.llm import LLMClient, UsageTracker  # noqa: E402


# ============================================================================
# A. THỐNG KÊ & ĐỘ ĐỒNG THUẬN (Cohen's κ, Spearman ρ) – viết tay, không phụ thuộc sklearn/scipy
# ============================================================================


# ----------------------------------------------------------------------------
# Thống kê mô tả
# ----------------------------------------------------------------------------


def mean(values: Iterable[Optional[float]]) -> Optional[float]:
    vals = [float(v) for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def percentile(values: Sequence[float], q: float) -> Optional[float]:
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return None
    pos = (len(vals) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def safe_div(a: float, b: float) -> Optional[float]:
    return a / b if b else None


# ----------------------------------------------------------------------------
# Độ đồng thuận (Human Eval vs LLM-as-a-Judge)
# ----------------------------------------------------------------------------


def cohen_kappa(a: Sequence[int], b: Sequence[int], weights: Optional[str] = "quadratic", labels: Optional[Sequence[int]] = None) -> Optional[float]:
    """Cohen's κ. weights=None (không trọng số) | 'linear' | 'quadratic' (khuyên dùng cho thang 1–5).

    κ = 1 − Σ w_ij·O_ij / Σ w_ij·E_ij ;  w_ij = 0 trên đường chéo.
    """
    if len(a) != len(b) or not a:
        return None
    labels = sorted(set(labels or []) | set(a) | set(b))
    idx = {v: i for i, v in enumerate(labels)}
    k, n = len(labels), len(a)
    if k == 1:
        return 1.0
    obs = [[0.0] * k for _ in range(k)]
    for x, y in zip(a, b):
        obs[idx[x]][idx[y]] += 1
    ra = [sum(row) for row in obs]
    rb = [sum(obs[i][j] for i in range(k)) for j in range(k)]

    def w(i, j):
        if weights is None:
            return 0.0 if i == j else 1.0
        d = abs(i - j) / (k - 1)
        return d if weights == "linear" else d * d

    num = sum(w(i, j) * obs[i][j] for i in range(k) for j in range(k))
    den = sum(w(i, j) * ra[i] * rb[j] / n for i in range(k) for j in range(k))
    if den == 0:
        return 1.0 if num == 0 else 0.0
    return 1 - num / den


def _rank(values: Sequence[float]) -> List[float]:
    """Hạng trung bình cho giá trị bằng nhau (ties)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for t in range(i, j + 1):
            ranks[order[t]] = avg
        i = j + 1
    return ranks


def pearson(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    n = len(x)
    if n != len(y) or n < 2:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def spearman_rho(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    """Spearman ρ = Pearson trên hạng (xử lý ties bằng hạng trung bình)."""
    if len(x) != len(y) or len(x) < 2:
        return None
    return pearson(_rank(list(x)), _rank(list(y)))


def interpret_kappa(k: Optional[float]) -> str:
    """Thang Landis & Koch (1977)."""
    if k is None:
        return "không xác định"
    if k < 0:
        return "kém (poor)"
    for cut, label in ((0.2, "rất thấp (slight)"), (0.4, "thấp (fair)"), (0.6, "trung bình (moderate)"), (0.8, "khá (substantial)")):
        if k <= cut:
            return label
    return "gần như tuyệt đối (almost perfect)"


def to_likert(score01: Optional[float]) -> Optional[int]:
    """0–1 → 1–5 (ngược với chuẩn hoá (s-1)/4 của Critic)."""
    if score01 is None:
        return None
    return int(round(1 + 4 * max(0.0, min(1.0, score01))))


def counter_top(items: Iterable[str], n: int = 10) -> List[tuple]:
    return Counter(items).most_common(n)


# ============================================================================
# B. BỘ CHẤM THỐNG NHẤT cho B1–B4
# Bộ chấm THỐNG NHẤT cho cả 4 cấu hình (B1–B4).
#
# Nguyên tắc công bằng: dù cấu hình có dùng AST/Sandbox trong vòng lặp hay không, KHI ĐÁNH GIÁ
# mọi giáo án đều được đo bằng cùng AST Guard + Sandbox + kiểm tra tất định + cùng LLM-as-a-Judge.
#
# Định nghĩa metric (dùng nguyên văn trong báo cáo):
# * out_of_scope_rate   = số phiếu có ≥1 lỗi AST (vượt phạm vi Bài k) / tổng số phiếu đã sinh.
# * code_pass_rate      = số phiếu PASS toàn bộ test_cases / (3 × số ca)  – phiếu thiếu/không hợp lệ tính là trượt.
# * test_case_pass_rate = số test PASS / tổng số test.
# * citation_precision  = trích dẫn hợp lệ / tổng trích dẫn (min với điểm LLM xác nhận nếu có).
# * faithfulness, yccd_coverage, differentiation_score: thang 0–1 (LLM-as-a-Judge + phần tất định).
# * recall_at_5, mrr    = theo trang SGK đúng của Bài k trong top-k chunk truy hồi (None với B1).
# * critic_miss_rate    = trong các ca Critic tuyên bố ĐẠT, tỉ lệ ca vẫn còn lỗi AST/Sandbox (đo độ "sót" của Critic).
# ============================================================================

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


# ============================================================================
# C. CHẠY ABLATION + LOG MLFLOW
# ============================================================================

DEFAULT_DATASET = ROOT / "eval" / "test_dataset.json"
DEFAULT_OUT = ROOT / "eval" / "results"
RESULTS = DEFAULT_OUT
ALL_CONFIGS = ("B1", "B2", "B3", "B4")

# Thứ tự cột & nhãn hiển thị của bảng Ablation (verify_report_metrics dùng chung bảng nhãn này)
TABLE_COLUMNS = [
    ("recall_at_5", "Recall@5", "float"),
    ("mrr", "MRR", "float"),
    ("faithfulness", "Faithfulness", "float"),
    ("citation_precision", "Citation Precision", "float"),
    ("yccd_coverage", "YCCĐ Coverage", "float"),
    ("out_of_scope_rate", "Out-of-Scope Rate", "pct"),
    ("code_pass_rate", "Code Pass Rate", "pct"),
    ("differentiation_score", "Differentiation", "float"),
    ("latency_s_mean", "Latency (s)", "sec"),
    ("tokens_per_case", "Tokens/ca", "int"),
    ("token_cost_usd", "Token Cost (USD)", "usd"),
]


def fmt_value(v, kind: str) -> str:
    if v is None:
        return "–"
    if kind == "pct":
        return f"{v * 100:.1f}%"
    if kind == "sec":
        return f"{v:.2f}"
    if kind == "int":
        return f"{v:,.0f}"
    if kind == "usd":
        return f"{v:.4f}"
    return f"{v:.3f}"


def render_table(summary: Dict[str, dict]) -> str:
    head = "| Cấu hình | " + " | ".join(label for _, label, _ in TABLE_COLUMNS) + " |"
    sep = "|" + "---|" * (len(TABLE_COLUMNS) + 1)
    rows = [head, sep]
    for cfg in [c for c in ALL_CONFIGS if c in summary] + [c for c in summary if c not in ALL_CONFIGS]:
        s = summary[cfg]
        rows.append(f"| {cfg} | " + " | ".join(fmt_value(s.get(k), kind) for k, _, kind in TABLE_COLUMNS) + " |")
    return "\n".join(rows)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def _instantiate(spec: Optional[str]):
    if not spec:
        return None
    obj = load_object(spec)
    return obj() if callable(obj) and not hasattr(obj, "complete") and not hasattr(obj, "retrieve") else obj


def _as_retriever(obj):
    if obj is None or hasattr(obj, "retrieve"):
        return obj
    if callable(obj):
        return FunctionRetriever(obj)
    raise TypeError("--retriever phải trả về object có .retrieve(request) hoặc 1 hàm fn(request)")


class _MlflowLogger:
    def __init__(self, enabled: bool, experiment: str, tracking_uri: Optional[str]):
        self.mlflow = None
        if not enabled:
            return
        try:
            import mlflow  # type: ignore

            if tracking_uri:
                mlflow.set_tracking_uri(tracking_uri)
            mlflow.set_experiment(experiment)
            self.mlflow = mlflow
        except ImportError:
            print("[warn] Chưa cài mlflow → chỉ ghi JSON. Cài: pip install mlflow", file=sys.stderr)

    def log(self, cfg: str, params: dict, metrics: dict, artifact: Path) -> Optional[str]:
        if self.mlflow is None:
            return None
        with self.mlflow.start_run(run_name=cfg) as run:
            self.mlflow.log_params({k: str(v)[:250] for k, v in params.items()})
            self.mlflow.log_metrics({k: float(v) for k, v in metrics.items() if isinstance(v, (int, float))})
            self.mlflow.log_artifact(str(artifact))
            return run.info.run_id


def run_benchmark(
    systems: Dict[str, object],
    dataset: List[dict],
    evaluator: Evaluator,
    trackers: Dict[str, UsageTracker],
    out_dir: Path,
    price_in: float = 0.0,
    price_out: float = 0.0,
    reference_provider: Optional[Callable] = None,
    progress: bool = True,
) -> Dict[str, List[CaseEvaluation]]:
    results: Dict[str, List[CaseEvaluation]] = {}
    for cfg, system in systems.items():
        tracker = trackers[cfg]
        plan_dir = out_dir / "plans" / cfg
        plan_dir.mkdir(parents=True, exist_ok=True)
        results[cfg] = []
        for i, case in enumerate(dataset):
            req = LessonRequest.model_validate({**case["request"], "bypass_hitl": True})
            tracker.reset()
            t0 = time.perf_counter()
            try:
                out: SystemOutput = system.generate(req)
            except Exception as exc:
                out = SystemOutput(plan=None, error=f"{type(exc).__name__}: {exc}")
                if progress:
                    traceback.print_exc()
            latency = time.perf_counter() - t0
            gen_in, gen_out, calls = tracker.input_tokens, tracker.output_tokens, tracker.calls
            refs = reference_provider(req) if reference_provider else ()
            ev = evaluator.evaluate(case, cfg, out, refs)
            ev.latency_s = round(latency, 3)
            ev.input_tokens, ev.output_tokens, ev.llm_calls = gen_in, gen_out, calls
            ev.cost_usd = round((gen_in * price_in + gen_out * price_out) / 1_000_000, 6)
            results[cfg].append(ev)
            (plan_dir / f"{case['case_id']}.json").write_text(
                json.dumps(
                    {
                        "case_id": case["case_id"],
                        "config": cfg,
                        "request": req.model_dump(),
                        "plan": out.plan.model_dump() if out.plan else None,
                        "retrieved_chunks": [c.model_dump() for c in out.retrieved_chunks],
                        "critic_history": out.critic_history,
                        "error": out.error,
                    },
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )
            if progress:
                print(
                    f"[{cfg}] {i + 1}/{len(dataset)} {case['case_id']}: valid={ev.json_valid} oos={ev.n_oos_worksheets}/{ev.n_worksheets} "
                    f"code_pass={ev.n_worksheets_code_pass}/3 iters={ev.iterations} {latency:.1f}s",
                    flush=True,
                )
    return results


def write_outputs(results: Dict[str, List[CaseEvaluation]], meta: dict, out_dir: Path, price_in: float, price_out: float,
                  mlflow_logger: Optional[_MlflowLogger] = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {cfg: summarize(evs, price_in, price_out) for cfg, evs in results.items()}
    payload = {"meta": meta, "summary": summary, "mlflow_runs": {}, "cases": {cfg: [e.to_dict() for e in evs] for cfg, evs in results.items()}}
    path = out_dir / "benchmark_results.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    if mlflow_logger is not None:
        for cfg, metrics in summary.items():
            run_id = mlflow_logger.log(cfg, {**meta, "config": cfg}, metrics, path)
            if run_id:
                payload["mlflow_runs"][cfg] = run_id
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    table = render_table(summary)
    (out_dir / "ablation_table.md").write_text(
        f"<!-- Sinh tự động bởi eval/run_benchmark.py lúc {meta.get('timestamp')} – KHÔNG sửa tay -->\n\n{table}\n", encoding="utf-8"
    )
    return payload


# ============================================================================
# D. HUMAN EVALUATION chấm mù B2 vs B4 + độ đồng thuận với LLM-as-a-Judge (phối hợp TV3)
#   python -m eval.run_benchmark human-eval export --n 6 --configs B2,B4
#   python -m eval.run_benchmark human-eval analyze --ratings eval/human_eval/ratings_*.csv
# ============================================================================

HE_DIR = ROOT / "eval" / "human_eval"
CRITERIA = {
    "faithfulness": "Bám tài liệu nguồn, không bịa kiến thức/vượt bài",
    "citation": "Trích dẫn (tài liệu, bài, trang) chính xác, hỗ trợ đúng nội dung",
    "yccd": "Bao phủ Yêu cầu cần đạt của bài",
    "timing": "Phân bổ thời lượng hợp lý, khớp 45/90 phút",
    "differentiation": "3 phiếu phân hoá rõ theo Bloom (Cơ bản – Đạt chuẩn – Nâng cao)",
}


# ----------------------------------------------------------------------------
# Render giáo án ẩn danh
# ----------------------------------------------------------------------------


def render_plan_md(item_id: str, request: dict, plan: Optional[dict]) -> str:
    lines = [f"# {item_id}", "", f"**Bài {request['target_bai']} – {request.get('topic_name', '')}** · {request['scope_mode']} · {request['target_minutes']} phút", ""]
    if not plan:
        return "\n".join(lines + ["_(Hệ thống không sinh được giáo án hợp lệ)_"])
    lines += ["## I. Mục tiêu", f"- **Kiến thức:** {plan.get('knowledge_goals', '')}", f"- **Năng lực chung:** {plan.get('general_competencies', '')}",
              f"- **Năng lực Tin học:** {plan.get('cs_competencies', '')}", f"- **Phẩm chất:** {plan.get('qualities', '')}", "",
              "## II. Thiết bị dạy học và học liệu", plan.get("teaching_materials", ""), "", "## III. Tiến trình dạy học"]
    for i, a in enumerate(plan.get("activities", []), start=1):
        lines += [f"### Hoạt động {i}: {a.get('activity_name', '')} ({a.get('duration_minutes', 0)} phút)",
                  f"- **Mục tiêu:** {a.get('goal', '')}", f"- **Nội dung:** {a.get('content', '')}", f"- **Sản phẩm:** {a.get('expected_product', '')}",
                  "- **Tổ chức thực hiện:**", f"  1. Chuyển giao: {a.get('step1_transfer', '')}", f"  2. Thực hiện: {a.get('step2_execute', '')}",
                  f"  3. Báo cáo, thảo luận: {a.get('step3_report', '')}", f"  4. Kết luận, nhận định: {a.get('step4_conclude', '')}"]
        for c in a.get("citations", []):
            lines.append(f"  - 📖 _{c.get('doc_name', '')}, Bài {c.get('bai_so', '')}, {c.get('section_title', '')}, tr.{c.get('page_number', '')}_: “{c.get('quoted_text', '')}”")
        lines.append("")
    lines.append("## Phụ lục – Phiếu học tập")
    for w in plan.get("worksheets", []):
        lines += [f"### Phiếu {w.get('tier', '')} ({w.get('bloom_level', '')})", w.get("prompt_text", ""), "```python", w.get("starter_code", ""), "```",
                  "<details><summary>Đáp án (GV)</summary>", "", "```python", w.get("solution_code", ""), "```", "</details>", ""]
    return "\n".join(lines)


def export_pack(n: int, configs: Sequence[str], seed: int, results_dir: Path = RESULTS, out_dir: Path = HE_DIR) -> dict:
    a_cfg, b_cfg = configs
    ids_a = {p.stem for p in (results_dir / "plans" / a_cfg).glob("*.json")}
    ids_b = {p.stem for p in (results_dir / "plans" / b_cfg).glob("*.json")}
    common = sorted(ids_a & ids_b)
    if not common:
        raise SystemExit(f"Không có case chung giữa {a_cfg} và {b_cfg} trong {results_dir / 'plans'}")
    rng = random.Random(seed)
    chosen = sorted(rng.sample(common, min(n, len(common))))
    items = []
    for cid in chosen:
        pair = [a_cfg, b_cfg]
        rng.shuffle(pair)
        for slot, cfg in zip("AB", pair):
            items.append({"case_id": cid, "config": cfg, "slot": slot})
    rng.shuffle(items)
    pack = out_dir / "pack"
    pack.mkdir(parents=True, exist_ok=True)
    key = {}
    for i, it in enumerate(items, start=1):
        item_id = f"ITEM_{i:02d}"
        rec = json.loads((results_dir / "plans" / it["config"] / f"{it['case_id']}.json").read_text(encoding="utf-8"))
        (pack / f"{item_id}.md").write_text(render_plan_md(item_id, rec["request"], rec.get("plan")), encoding="utf-8")
        key[item_id] = it
    (out_dir / "key.json").write_text(json.dumps({"seed": seed, "configs": list(configs), "items": key}, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(out_dir / "ratings_template.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rater_id", "item_id", "criterion", "score"])
        for item_id in key:
            for crit in CRITERIA:
                w.writerow(["R1", item_id, crit, ""])
    (out_dir / "RUBRIC.md").write_text(
        "# Rubric chấm mù (thang 1–5)\n\n1 = rất kém · 2 = kém · 3 = đạt một phần · 4 = tốt · 5 = hoàn toàn đạt\n\n"
        + "\n".join(f"- **{k}**: {v}" for k, v in CRITERIA.items()),
        encoding="utf-8",
    )
    return key


# ----------------------------------------------------------------------------
# Phân tích độ đồng thuận
# ----------------------------------------------------------------------------


def load_ratings(paths: Sequence[str]) -> List[dict]:
    rows = []
    for pattern in paths:
        for p in sorted(glob.glob(pattern)) or [pattern]:
            with open(p, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    if str(r.get("score", "")).strip():
                        rows.append({"rater": r["rater_id"].strip(), "item": r["item_id"].strip(), "criterion": r["criterion"].strip(), "score": int(float(r["score"]))})
    return rows


def judge_scores(results_path: Path) -> Dict[tuple, Dict[str, int]]:
    payload = json.loads(results_path.read_text(encoding="utf-8"))
    out = {}
    for cfg, cases in payload.get("cases", {}).items():
        for c in cases:
            if c.get("judge_likert"):
                out[(cfg, c["case_id"])] = c["judge_likert"]
    return out


def _agreement(h: List[float], l: List[int]) -> dict:
    h_int = [int(round(x)) for x in h]
    k = cohen_kappa(h_int, l, weights="quadratic", labels=[1, 2, 3, 4, 5])
    rho = spearman_rho(h, [float(x) for x in l])
    return {"n": len(h), "kappa": None if k is None else round(k, 4), "kappa_level": interpret_kappa(k),
            "spearman_rho": None if rho is None else round(rho, 4)}


def analyze(rating_paths: Sequence[str], key_path: Path = HE_DIR / "key.json", results_path: Path = RESULTS / "benchmark_results.json") -> dict:
    ratings = load_ratings(rating_paths)
    key = json.loads(key_path.read_text(encoding="utf-8"))["items"]
    judge = judge_scores(results_path)

    by_item_crit: Dict[tuple, Dict[str, int]] = defaultdict(dict)
    for r in ratings:
        by_item_crit[(r["item"], r["criterion"])][r["rater"]] = r["score"]
    raters = sorted({r["rater"] for r in ratings})

    per_crit_h, per_crit_l = defaultdict(list), defaultdict(list)
    pooled_h, pooled_l = [], []
    by_config = defaultdict(lambda: defaultdict(list))
    missing_judge = 0
    for (item, crit), scores in by_item_crit.items():
        info = key.get(item)
        if not info:
            continue
        h = mean(scores.values())
        by_config[info["config"]][crit].append(h)
        lj = judge.get((info["config"], info["case_id"]), {}).get(crit)
        if lj is None:
            missing_judge += 1
            continue
        per_crit_h[crit].append(h)
        per_crit_l[crit].append(int(lj))
        pooled_h.append(h)
        pooled_l.append(int(lj))

    inter = {}
    if len(raters) >= 2:
        r1, r2 = raters[:2]
        pairs = [(s[r1], s[r2]) for s in by_item_crit.values() if r1 in s and r2 in s]
        if pairs:
            k = cohen_kappa([a for a, _ in pairs], [b for _, b in pairs], "quadratic", [1, 2, 3, 4, 5])
            inter = {"raters": [r1, r2], "n": len(pairs), "kappa": None if k is None else round(k, 4), "kappa_level": interpret_kappa(k)}

    summary = {
        "overall": _agreement(pooled_h, pooled_l) if pooled_h else {},
        "per_criterion": {c: _agreement(per_crit_h[c], per_crit_l[c]) for c in CRITERIA if per_crit_h[c]},
        "inter_rater": inter,
        "human_mean_by_config": {cfg: {c: round(mean(v), 4) for c, v in crits.items()} for cfg, crits in by_config.items()},
    }
    result = {"summary": summary, "n_ratings": len(ratings), "raters": raters, "missing_judge_scores": missing_judge,
              "method": "Cohen's κ trọng số bậc hai (quadratic) trên thang 1–5, điểm người = trung bình các người chấm (làm tròn); Spearman ρ trên điểm trung bình."}
    out = RESULTS / "human_eval_agreement.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


# ============================================================================
# E. ERROR ANALYSIS → eval/results/error_analysis.md
#   python -m eval.run_benchmark error-analysis
# ============================================================================

def build_report(payload: dict, max_examples: int = 8) -> str:
    cases = payload.get("cases", {})
    out = [f"# Error Analysis – Ablation ({payload.get('meta', {}).get('base_model', '?')})", "",
           "_Sinh tự động bởi `eval/run_benchmark.py error-analysis`. Mọi con số lấy từ `benchmark_results.json`._", ""]

    out += ["## 1. Cấu trúc vượt phạm vi hay gặp nhất (AST Guard đo trên MỌI cấu hình)", "",
            "| Cấu hình | Top vi phạm (tầng:tên × số lần) |", "|---|---|"]
    for cfg, evs in cases.items():
        cnt = Counter(n for e in evs for n in e.get("oos_violation_names", []))
        out.append(f"| {cfg} | " + (", ".join(f"`{k}` ×{v}" for k, v in cnt.most_common(8)) or "—") + " |")

    out += ["", "## 2. Kết quả Sandbox theo trạng thái test case", "", "| Cấu hình | PASS | WRONG_ANSWER | RUNTIME_ERROR | TIMEOUT | SYNTAX_ERROR |", "|---|---|---|---|---|---|"]
    for cfg, evs in cases.items():
        cnt = Counter(s for e in evs for s in e.get("sandbox_statuses", []))
        out.append(f"| {cfg} | " + " | ".join(str(cnt.get(k, 0)) for k in ("PASS", "WRONG_ANSWER", "RUNTIME_ERROR", "TIMEOUT", "SYNTAX_ERROR")) + " |")

    out += ["", "## 3. Critic tuyên bố ĐẠT nhưng vẫn còn lỗi code (Critic miss)", ""]
    for cfg, evs in cases.items():
        claimed = [e for e in evs if e.get("critic_passed")]
        if not claimed and not any(e.get("critic_passed") is not None for e in evs):
            continue
        missed = [e for e in claimed if e.get("n_oos_worksheets", 0) > 0 or e.get("n_worksheets_code_pass", 0) < 3]
        out.append(f"- **{cfg}**: {len(missed)}/{len(claimed)} ca Critic báo đạt nhưng kiểm định tất định còn lỗi.")
        for e in missed[:max_examples]:
            first = (e.get("oos_messages") or []) + (e.get("sandbox_messages") or [])
            out.append(f"  - `{e['case_id']}` (Bài {e['target_bai']}): " + (first[0] if first else "phiếu thiếu/không hợp lệ"))

    out += ["", "## 4. Số vòng lặp Designer ↔ Critic", "", "| Cấu hình | 1 vòng | 2 vòng | 3 vòng | TB |", "|---|---|---|---|---|"]
    for cfg, evs in cases.items():
        it = Counter(int(e.get("iterations", 1)) for e in evs)
        avg = sum(int(e.get("iterations", 1)) for e in evs) / max(1, len(evs))
        out.append(f"| {cfg} | {it.get(1, 0)} | {it.get(2, 0)} | {it.get(3, 0)} | {avg:.2f} |")

    out += ["", "## 5. Ca lỗi định dạng / thời lượng / trích dẫn", ""]
    for cfg, evs in cases.items():
        invalid = [e["case_id"] for e in evs if not e.get("json_valid")]
        bad_time = [e["case_id"] for e in evs if e.get("json_valid") and not e.get("duration_ok")]
        leaks = sum(e.get("citation_leaks", 0) for e in evs)
        out.append(f"- **{cfg}**: JSON lỗi {len(invalid)} ca{(' (' + ', '.join(invalid[:5]) + ')') if invalid else ''}; "
                   f"lệch thời lượng {len(bad_time)} ca; {leaks} trích dẫn vượt bài.")
    out += ["", "## 6. Nhận định (điền tay sau khi đọc các ví dụ trên)", "", "- …", ""]
    return "\n".join(out)


def human_eval_main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--n", type=int, default=6)
    e.add_argument("--configs", default="B2,B4")
    e.add_argument("--seed", type=int, default=2025)
    a = sub.add_parser("analyze")
    a.add_argument("--ratings", nargs="+", required=True)
    a.add_argument("--key", default=str(HE_DIR / "key.json"))
    a.add_argument("--results", default=str(RESULTS / "benchmark_results.json"))
    args = ap.parse_args(argv)
    if args.cmd == "export":
        key = export_pack(args.n, [c.strip() for c in args.configs.split(",")], args.seed)
        print(f"Đã xuất {len(key)} giáo án ẩn danh → {HE_DIR / 'pack'}  (giữ kín {HE_DIR / 'key.json'})")
    else:
        res = analyze(args.ratings, Path(args.key), Path(args.results))
        print(json.dumps(res["summary"], ensure_ascii=False, indent=1))
    return 0


def error_analysis_main(argv: Optional[Sequence[str]] = None) -> int:
    path = Path(argv[0]) if argv else RESULTS / "benchmark_results.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    md = build_report(payload)
    (path.parent / "error_analysis.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["human-eval"]:
        return human_eval_main(argv[1:])
    if argv[:1] == ["error-analysis"]:
        return error_analysis_main(argv[1:])
    ap = argparse.ArgumentParser(description="Ablation B1–B4 + MLflow")
    ap.add_argument("--configs", default=",".join(ALL_CONFIGS))
    ap.add_argument("--dataset", default=str(DEFAULT_DATASET))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--llm", default="src.utils.llm:llm_from_env", help="module:factory trả về LLMClient (temperature=0)")
    ap.add_argument("--judge-llm", default=None, help="LLM chấm (mặc định = --llm). Nên dùng model khác để giảm self-bias.")
    ap.add_argument("--no-judge", action="store_true", help="Bỏ LLM-as-a-Judge (chỉ metric tất định)")
    ap.add_argument("--retriever", default=None, help="module:factory trả về Retriever hoặc fn(request)->chunks (TV2)")
    ap.add_argument("--reference", default=None, help="module:factory tài liệu chuẩn cho Judge (mặc định = chunk truy hồi)")
    ap.add_argument("--graph", default=None, help="module:build_graph của TV1 (mặc định src.graph.workflow)")
    ap.add_argument("--fallback-loop", action="store_true", help="B3/B4 dùng vòng Designer↔Critic mô phỏng (trước tích hợp)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--case", action="append", default=[], help="Chỉ chạy case_id này (lặp lại được)")
    ap.add_argument("--price-in", type=float, default=0.0, help="USD / 1M token input")
    ap.add_argument("--price-out", type=float, default=0.0, help="USD / 1M token output")
    ap.add_argument("--no-mlflow", action="store_true")
    ap.add_argument("--experiment", default="ablation_tin10_kntt")
    ap.add_argument("--tracking-uri", default=None)
    ap.add_argument("--temperature", type=float, default=0.0)
    args = ap.parse_args(argv)

    configs = [c.strip().upper() for c in args.configs.split(",") if c.strip()]
    dataset_path = Path(args.dataset)
    data = json.loads(dataset_path.read_text(encoding="utf-8"))
    cases = data["cases"] if isinstance(data, dict) else data
    if args.case:
        cases = [c for c in cases if c["case_id"] in set(args.case)]
    if args.limit:
        cases = cases[: args.limit]

    base_llm = _instantiate(args.llm)
    judge_base = _instantiate(args.judge_llm) if args.judge_llm else base_llm
    retriever = _as_retriever(_instantiate(args.retriever))
    reference = _as_retriever(_instantiate(args.reference))
    graph_factory = load_object(args.graph) if args.graph else None

    trackers = {cfg: UsageTracker(base_llm) for cfg in configs}
    systems = {}
    for cfg in configs:
        systems.update(build_systems([cfg], trackers[cfg], retriever, graph_factory, args.fallback_loop))
    judge = None if args.no_judge else UsageTracker(judge_base).for_component("judge")
    evaluator = Evaluator(judge_llm=judge)

    meta = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base_model": getattr(base_llm, "model_name", "unknown"),
        "judge_model": "none" if judge is None else getattr(judge_base, "model_name", "unknown"),
        "temperature": args.temperature,
        "bypass_hitl": True,
        "dataset": str(dataset_path.relative_to(ROOT) if dataset_path.is_relative_to(ROOT) else dataset_path),
        "dataset_sha256": sha256_file(dataset_path),
        "n_cases": len(cases),
        "configs": configs,
        "fallback_loop": args.fallback_loop,
        "git_commit": git_commit(),
        "price_in_per_mtok": args.price_in,
        "price_out_per_mtok": args.price_out,
    }
    out_dir = Path(args.out)
    results = run_benchmark(systems, cases, evaluator, trackers, out_dir, args.price_in, args.price_out,
                            reference.retrieve if reference else None)
    payload = write_outputs(results, meta, out_dir, args.price_in, args.price_out,
                            None if args.no_mlflow else _MlflowLogger(True, args.experiment, args.tracking_uri))
    print("\n" + render_table(payload["summary"]))
    print(f"\n→ {out_dir / 'benchmark_results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
