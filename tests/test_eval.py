"""Test metric, benchmark end-to-end (LLM giả lập), kiểm toán số liệu và Human Eval."""

import csv
import json
from pathlib import Path

import pytest

from eval import run_benchmark as human_eval  # Human Eval nằm trong run_benchmark.py
from eval import verify_report_metrics
from eval.baselines import GEN_SYSTEM, FunctionRetriever, build_systems
from eval.run_benchmark import Evaluator
from eval.retrieval_eval import recall_at_k, reciprocal_rank
from eval.run_benchmark import cohen_kappa, spearman_rho
from eval.run_benchmark import render_table, run_benchmark, write_outputs
from src.schemas import SourceCitation
from src.utils.llm import UsageTracker

FIX = Path(__file__).parent / "mocks"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- metrics


def test_retrieval_metrics():
    assert recall_at_k([105, 1, 106, 2, 3, 107], {105, 106, 107}, 5) == pytest.approx(2 / 3)
    assert reciprocal_rank([1, 2, 106], {105, 106}) == pytest.approx(1 / 3)
    assert reciprocal_rank([1, 2], {105}) == 0.0
    assert recall_at_k([1], set()) is None


def test_kappa_and_spearman():
    assert cohen_kappa([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]) == pytest.approx(1.0)
    # ví dụ kinh điển không trọng số: 2 người, 2 nhãn
    a = [1] * 20 + [1] * 5 + [2] * 10 + [2] * 15
    b = [1] * 20 + [2] * 5 + [1] * 10 + [2] * 15
    assert cohen_kappa(a, b, weights=None) == pytest.approx(0.4)
    assert cohen_kappa([1, 2, 3], [3, 2, 1], weights="quadratic") < 0
    assert spearman_rho([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman_rho([1, 2, 2, 3], [1, 3, 2, 4]) == pytest.approx(0.9487, abs=1e-4)  # có ties


# ---------------------------------------------------------------- benchmark E2E


class RoutedLLM:
    """Designer trả lần lượt các giáo án; Critic/Judge trả điểm tối đa."""

    model_name = "fake-llm"

    def __init__(self, designer_plans):
        self.designer_plans = list(designer_plans)
        self.n_design = 0

    def complete(self, system, user):
        from src.utils.llm import LLMResponse

        if system == GEN_SYSTEM:
            plan = self.designer_plans[min(self.n_design, len(self.designer_plans) - 1)]
            self.n_design += 1
            return LLMResponse(text="```json\n" + json.dumps(plan, ensure_ascii=False) + "\n```", input_tokens=1000, output_tokens=800)
        reply = {k: {"score": 5} for k in ("faithfulness", "citation_support", "timing", "differentiation")}
        reply["yccd"] = {"score": 5, "covered": []}
        return LLMResponse(text=json.dumps(reply), input_tokens=500, output_tokens=100)


def code_violation_plan():
    plan = load("plan_bai20_clean.json")
    plan["worksheets"][2]["solution_code"] = "n = int(input())\nA = [int(input()) for i in range(n)]\nprint(max(A))"
    return plan


@pytest.fixture
def bench(tmp_path, monkeypatch):
    monkeypatch.setattr("src.rag.ingest.YCCD_PATH", tmp_path / "none.json")
    dataset = [{"case_id": "B20_DT_45mTH", "request": load("request_bai20.json")}]
    chunks = [SourceCitation.model_validate(c) for c in load("chunks_bai20.json")]
    retriever = FunctionRetriever(lambda req: chunks)
    llms = {
        "B1": RoutedLLM([load("plan_bai20_violations.json")]),
        "B2": RoutedLLM([code_violation_plan()]),
        "B3": RoutedLLM([code_violation_plan(), load("plan_bai20_clean.json")]),
        "B4": RoutedLLM([code_violation_plan(), load("plan_bai20_clean.json")]),
    }
    trackers = {cfg: UsageTracker(llm) for cfg, llm in llms.items()}
    systems = {}
    for cfg in llms:
        systems.update(build_systems([cfg], trackers[cfg], retriever, use_fallback_loop=True))
    evaluator = Evaluator(judge_llm=RoutedLLM([{}]), yccd_loader=lambda b: [])
    results = run_benchmark(systems, dataset, evaluator, trackers, tmp_path, price_in=0.1, price_out=0.4, progress=False)
    payload = write_outputs(results, {"timestamp": "test", "base_model": "fake-llm"}, tmp_path, 0.1, 0.4)
    return tmp_path, payload


def test_ablation_separates_contributions(bench):
    out_dir, payload = bench
    s = payload["summary"]
    # B1: không RAG → không có Recall; nhiều lỗi
    assert s["B1"]["recall_at_5"] is None and s["B1"]["out_of_scope_rate"] > 0 and s["B1"]["code_pass_rate"] < 1
    # B2: có RAG nhưng không Critic → vẫn vượt phạm vi
    assert s["B2"]["recall_at_5"] == 1.0 and s["B2"]["out_of_scope_rate"] == pytest.approx(1 / 3, abs=1e-4)
    # B3: LLM Critic thuần "tưởng" đạt ngay vòng 1 → vẫn sót lỗi → critic_miss_rate = 1
    assert s["B3"]["avg_iterations"] == 1 and s["B3"]["critic_miss_rate"] == 1.0 and s["B3"]["out_of_scope_rate"] > 0
    # B4: AST bắt lỗi → Revision → vòng 2 sạch
    assert s["B4"]["out_of_scope_rate"] == 0.0 and s["B4"]["code_pass_rate"] == 1.0
    assert s["B4"]["avg_iterations"] == 2 and s["B4"]["critic_miss_rate"] == 0.0
    assert s["B4"]["token_cost_usd"] > 0
    assert (out_dir / "ablation_table.md").exists() and (out_dir / "plans" / "B4" / "B20_DT_45mTH.json").exists()


def test_verify_report_pass_and_fail(bench, tmp_path):
    out_dir, payload = bench
    report = tmp_path / "bao_cao.md"
    report.write_text(
        "# Kết quả\n\n" + render_table(payload["summary"]) + "\n\nB4 đạt [[B4.out_of_scope_rate=0%]] và [[B4.code_pass_rate=100%]].\n",
        encoding="utf-8",
    )
    checks, code = verify_report_metrics.run(out_dir / "benchmark_results.json", None, [report], None, with_mlflow=False)
    assert code == 0, [c for c in checks if c.status != "PASS"]
    assert len(checks) > 20

    tampered = report.read_text(encoding="utf-8").replace("[[B4.code_pass_rate=100%]]", "[[B4.code_pass_rate=98%]]")
    report.write_text(tampered, encoding="utf-8")
    checks, code = verify_report_metrics.run(out_dir / "benchmark_results.json", None, [report], None, with_mlflow=False)
    assert code == 1 and any(c.status == "MISMATCH" and c.key == "B4.code_pass_rate" for c in checks)

    claims = tmp_path / "claims.json"
    claims.write_text(json.dumps([{"key": "B9.mrr", "value": "0.9"}]), encoding="utf-8")
    checks, code = verify_report_metrics.run(out_dir / "benchmark_results.json", None, [], claims, with_mlflow=False)
    assert code == 1 and checks[0].status == "NOT_FOUND"


@pytest.mark.parametrize(
    "text,expected",
    [("93.3%", (93.3, 1, True)), ("0,85", (0.85, 2, False)), ("1,234", (1234.0, 0, False)), ("0.123", (0.123, 3, False)), ("0,123", (0.123, 3, False)), ("**100 %**", (100.0, 0, True))],
)
def test_parse_number(text, expected):
    assert verify_report_metrics.parse_number(text) == expected


def test_human_eval_roundtrip(bench, tmp_path, monkeypatch):
    out_dir, _ = bench
    he_dir = tmp_path / "he"
    monkeypatch.setattr(human_eval, "RESULTS", out_dir)
    key = human_eval.export_pack(1, ["B2", "B4"], seed=1, results_dir=out_dir, out_dir=he_dir)
    assert len(key) == 2 and (he_dir / "pack" / "ITEM_01.md").exists()
    text = (he_dir / "pack" / "ITEM_01.md").read_text(encoding="utf-8")
    assert "B2" not in text and "B4" not in text  # chấm mù
    ratings = he_dir / "ratings.csv"
    with open(ratings, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rater_id", "item_id", "criterion", "score"])
        for item_id in key:
            for crit in human_eval.CRITERIA:
                w.writerow(["R1", item_id, crit, 5])
                w.writerow(["R2", item_id, crit, 4])
    res = human_eval.analyze([str(ratings)], he_dir / "key.json", out_dir / "benchmark_results.json")
    assert res["n_ratings"] == 20 and res["summary"]["overall"]["n"] == 10
    assert set(res["summary"]["human_mean_by_config"]) == {"B2", "B4"}
    assert res["summary"]["inter_rater"]["n"] == 10
