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
import hashlib
import json
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.baselines import FunctionRetriever, SystemOutput, build_systems, load_object  # noqa: E402
from eval.evaluator import CaseEvaluation, Evaluator, summarize  # noqa: E402
from src.schemas import LessonRequest  # noqa: E402
from src.utils.llm import UsageTracker  # noqa: E402

DEFAULT_DATASET = ROOT / "eval" / "test_dataset.json"
DEFAULT_OUT = ROOT / "eval" / "results"
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


def main(argv: Optional[Sequence[str]] = None) -> int:
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
