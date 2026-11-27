"""Human Evaluation chấm mù + độ đồng thuận với LLM-as-a-Judge (phối hợp TV3).

Bước 1 – xuất gói chấm mù (sau khi chạy benchmark):
    python -m eval.human_eval export --n 6 --configs B2,B4 --seed 2025
  → eval/human_eval/pack/ITEM_XX.md   (giáo án ẩn danh, KHÔNG có nhãn cấu hình)
  → eval/human_eval/ratings_template.csv  (gửi người chấm điền điểm 1–5)
  → eval/human_eval/key.json          (khoá giải mã – KHÔNG gửi người chấm)

Bước 2 – người chấm điền CSV: rater_id,item_id,criterion,score   (criterion ∈ CRITERIA)

Bước 3 – phân tích:
    python -m eval.human_eval analyze --ratings eval/human_eval/ratings_*.csv
  → eval/results/human_eval_agreement.json  (κ có trọng số bậc hai, Spearman ρ, κ giữa 2 người chấm, B2 vs B4)
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.metrics import cohen_kappa, interpret_kappa, mean, spearman_rho  # noqa: E402

RESULTS = ROOT / "eval" / "results"
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


def main(argv: Optional[Sequence[str]] = None) -> int:
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


if __name__ == "__main__":
    raise SystemExit(main())
