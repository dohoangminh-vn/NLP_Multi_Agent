"""Kiểm toán số liệu báo cáo/slide: mọi con số phải khớp 100% file log JSON (và MLflow nếu có).

Nguồn "khẳng định" (claims) được kiểm:
  1. File claims JSON  : [{"key": "B4.code_pass_rate", "value": "100%", "where": "Slide 12"}, ...]
  2. Bảng Markdown     : bảng có cột đầu là cấu hình (B1..B4) và tiêu đề cột là tên metric
                         (vd bảng dán từ eval/results/ablation_table.md vào báo cáo).
  3. Bảng trong .docx / .pptx (cần python-docx / python-pptx).
  4. Thẻ nội dòng trong .md/.txt: `[[B4.out_of_scope_rate=0%]]` hoặc `[[human_eval.overall.kappa=0.71]]`.

So khớp theo độ chính xác HIỂN THỊ: "93.3%" khớp nếu |giá trị thật×100 − 93.3| ≤ 0.05.
Chấp nhận dấu thập phân kiểu Việt Nam ("0,93").

    python -m eval.verify_report_metrics --doc report/bao_cao.md --doc slides/slide.pptx --claims eval/report_claims.json
    python -m eval.verify_report_metrics --render-table     # in bảng chuẩn để dán vào báo cáo

Thoát mã 0 khi PASS 100%, mã 1 nếu có sai lệch / không tìm thấy metric, mã 2 nếu không có claim nào.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.run_benchmark import TABLE_COLUMNS, render_table  # noqa: E402

DEFAULT_RESULTS = ROOT / "eval" / "results" / "benchmark_results.json"
DEFAULT_HUMAN = ROOT / "eval" / "results" / "human_eval_agreement.json"
CONFIG_RE = re.compile(r"\b(B[1-4])\b")
INLINE_RE = re.compile(r"\[\[\s*([A-Za-z0-9_.@-]+)\s*=\s*([^\]]+?)\s*\]\]")
NUM_RE = re.compile(r"[-+]?\d[\d.,\s]*")

# Nhãn cột → khoá metric (chuẩn hoá bỏ dấu, chữ thường)
_ALIASES = {
    "recall@5": "recall_at_5", "recall_at_5": "recall_at_5", "recall5": "recall_at_5",
    "mrr": "mrr",
    "faithfulness": "faithfulness", "do trung thuc": "faithfulness",
    "citation precision": "citation_precision", "citation_precision": "citation_precision",
    "yccd coverage": "yccd_coverage", "yccd_coverage": "yccd_coverage", "do phu yccd": "yccd_coverage",
    "out-of-scope rate": "out_of_scope_rate", "out-of-scope violation rate": "out_of_scope_rate",
    "out_of_scope_rate": "out_of_scope_rate", "oos rate": "out_of_scope_rate", "ty le vuot chuong trinh": "out_of_scope_rate",
    "code pass rate": "code_pass_rate", "code_pass_rate": "code_pass_rate",
    "differentiation": "differentiation_score", "differentiation score": "differentiation_score",
    "differentiation_score": "differentiation_score",
    "latency (s)": "latency_s_mean", "latency": "latency_s_mean", "latency_s_mean": "latency_s_mean",
    "tokens/ca": "tokens_per_case", "tokens_per_case": "tokens_per_case",
    "token cost (usd)": "token_cost_usd", "token cost": "token_cost_usd", "token_cost_usd": "token_cost_usd",
    "test case pass rate": "test_case_pass_rate", "duration match rate": "duration_match_rate",
    "critic miss rate": "critic_miss_rate", "critic_miss_rate": "critic_miss_rate",
    "avg iterations": "avg_iterations", "so vong lap tb": "avg_iterations",
    "json valid rate": "json_valid_rate",
}
for _k, _label, _ in TABLE_COLUMNS:
    _ALIASES.setdefault(_label.lower(), _k)


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFD", text or "")
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn").replace("đ", "d").replace("Đ", "D")
    return " ".join(t.lower().replace("*", "").replace("`", "").split())


def metric_key(header: str) -> Optional[str]:
    h = _norm(header)
    if h in _ALIASES:
        return _ALIASES[h]
    h2 = re.sub(r"\s*\(.*?\)\s*$", "", h)
    return _ALIASES.get(h2)


@dataclass
class Claim:
    key: str
    text: str
    where: str


@dataclass
class Check:
    key: str
    claimed: str
    actual: Optional[float]
    status: str  # PASS | MISMATCH | NOT_FOUND | UNPARSABLE
    where: str
    detail: str = ""


# ----------------------------------------------------------------------------
# Nguồn sự thật
# ----------------------------------------------------------------------------


def flatten(obj, prefix: str = "") -> Dict[str, float]:
    out: Dict[str, float] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, bool):
        pass
    elif isinstance(obj, (int, float)):
        out[prefix] = float(obj)
    return out


def load_truth(results_path: Path, human_path: Optional[Path]) -> Tuple[Dict[str, float], dict]:
    payload = json.loads(results_path.read_text(encoding="utf-8"))
    truth = flatten(payload.get("summary", {}))
    if human_path and human_path.exists():
        truth.update(flatten(json.loads(human_path.read_text(encoding="utf-8")).get("summary", {}), "human_eval"))
    return truth, payload


# ----------------------------------------------------------------------------
# Trích claim
# ----------------------------------------------------------------------------


def parse_number(text: str) -> Tuple[Optional[float], int, bool]:
    """'93.3%' → (93.3, 1, True); '0,85' → (0.85, 2, False). Trả (giá trị, số chữ số thập phân, là_phần_trăm)."""
    t = (text or "").strip()
    is_pct = "%" in t
    m = NUM_RE.search(t.replace(" ", " "))
    if not m:
        return None, 0, is_pct
    raw = m.group(0).strip().replace(" ", "")
    if "," in raw and "." in raw:  # 1,234.5 hoặc 1.234,5
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        parts = raw.split(",")
        is_decimal = len(parts) == 2 and (len(parts[1]) != 3 or parts[0].lstrip("+-") == "0")
        raw = raw.replace(",", ".") if is_decimal else raw.replace(",", "")
    try:
        val = float(raw)
    except ValueError:
        return None, 0, is_pct
    decimals = len(raw.split(".")[1]) if "." in raw else 0
    return val, decimals, is_pct


def claims_from_markdown_tables(text: str, where: str) -> List[Claim]:
    claims: List[Claim] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            header = [c.strip() for c in lines[i].strip().strip("|").split("|")]
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                claims.extend(_claims_from_row(header, cells, f"{where}:dòng {j + 1}"))
                j += 1
            i = j
        else:
            i += 1
    return claims


def _claims_from_row(header: Sequence[str], cells: Sequence[str], where: str) -> List[Claim]:
    if not cells:
        return []
    m = CONFIG_RE.search(cells[0])
    if not m:
        return []
    cfg = m.group(1)
    out = []
    for h, c in zip(header[1:], cells[1:]):
        key = metric_key(h)
        if key and c and c not in ("–", "-", "—", "N/A", "n/a"):
            out.append(Claim(f"{cfg}.{key}", c, where))
    return out


def claims_inline(text: str, where: str) -> List[Claim]:
    return [Claim(m.group(1), m.group(2), where) for m in INLINE_RE.finditer(text)]


def claims_from_docx(path: Path) -> List[Claim]:
    import docx  # python-docx

    d = docx.Document(str(path))
    claims: List[Claim] = []
    for ti, table in enumerate(d.tables):
        rows = [[c.text.strip() for c in r.cells] for r in table.rows]
        if rows:
            for ri, row in enumerate(rows[1:], start=2):
                claims.extend(_claims_from_row(rows[0], row, f"{path.name}:bảng {ti + 1} hàng {ri}"))
    full = "\n".join(p.text for p in d.paragraphs)
    claims.extend(claims_inline(full, path.name))
    return claims


def claims_from_pptx(path: Path) -> List[Claim]:
    from pptx import Presentation  # python-pptx

    prs = Presentation(str(path))
    claims: List[Claim] = []
    for si, slide in enumerate(prs.slides, start=1):
        texts = []
        for shape in slide.shapes:
            if getattr(shape, "has_table", False) and shape.has_table:
                rows = [[c.text.strip() for c in r.cells] for r in shape.table.rows]
                for ri, row in enumerate(rows[1:], start=2):
                    claims.extend(_claims_from_row(rows[0], row, f"{path.name}:slide {si} hàng {ri}"))
            if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                texts.append(shape.text_frame.text)
        claims.extend(claims_inline("\n".join(texts), f"{path.name}:slide {si}"))
    return claims


def collect_claims(docs: Iterable[Path], claims_file: Optional[Path]) -> List[Claim]:
    claims: List[Claim] = []
    if claims_file:
        for c in json.loads(claims_file.read_text(encoding="utf-8")):
            claims.append(Claim(c["key"], str(c["value"]), c.get("where", claims_file.name)))
    for p in docs:
        suffix = p.suffix.lower()
        if suffix == ".docx":
            claims.extend(claims_from_docx(p))
        elif suffix == ".pptx":
            claims.extend(claims_from_pptx(p))
        else:
            text = p.read_text(encoding="utf-8")
            claims.extend(claims_from_markdown_tables(text, p.name))
            claims.extend(claims_inline(text, p.name))
    return claims


# ----------------------------------------------------------------------------
# Đối soát
# ----------------------------------------------------------------------------

_RATE_HINT = ("rate", "precision", "recall", "mrr", "faithfulness", "coverage", "differentiation", "kappa", "rho")


def check_claim(claim: Claim, truth: Dict[str, float]) -> Check:
    actual = truth.get(claim.key)
    if actual is None:
        return Check(claim.key, claim.text, None, "NOT_FOUND", claim.where, "Không có metric này trong file log")
    val, decimals, is_pct = parse_number(claim.text)
    if val is None:
        return Check(claim.key, claim.text, actual, "UNPARSABLE", claim.where)
    is_rate = any(h in claim.key.split(".")[-1] for h in _RATE_HINT)
    target = actual * 100 if (is_pct or (is_rate and val > 1.0 and abs(actual) <= 1.0)) else actual
    tol = 0.5 * 10 ** (-decimals) + 1e-9
    ok = abs(target - val) <= tol
    detail = f"thực tế = {target:.{max(decimals, 1) + 2}f}{'%' if is_pct else ''} (dung sai ±{tol:g})"
    return Check(claim.key, claim.text, actual, "PASS" if ok else "MISMATCH", claim.where, detail)


def check_mlflow(payload: dict) -> List[Check]:
    runs = payload.get("mlflow_runs") or {}
    if not runs:
        return []
    try:
        import mlflow  # type: ignore
    except ImportError:
        return [Check("mlflow", "-", None, "NOT_FOUND", "MLflow", "Chưa cài mlflow – bỏ qua đối soát MLflow")]
    checks = []
    for cfg, run_id in runs.items():
        logged = mlflow.get_run(run_id).data.metrics
        for k, v in (payload["summary"].get(cfg) or {}).items():
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            mv = logged.get(k)
            status = "PASS" if mv is not None and abs(float(mv) - float(v)) <= 1e-6 else "MISMATCH"
            checks.append(Check(f"{cfg}.{k}", f"{v}", mv, status, f"MLflow run {run_id[:8]}", "JSON vs MLflow"))
    return checks


def run(results: Path, human: Optional[Path], docs: Sequence[Path], claims_file: Optional[Path], with_mlflow: bool = True) -> Tuple[List[Check], int]:
    truth, payload = load_truth(results, human)
    claims = collect_claims(docs, claims_file)
    checks = [check_claim(c, truth) for c in claims]
    if with_mlflow:
        checks.extend(check_mlflow(payload))
    doc_checks = [c for c in checks if not c.where.startswith("MLflow")]
    if not doc_checks:
        return checks, 2
    bad = [c for c in checks if c.status != "PASS" and not (c.key == "mlflow" and c.status == "NOT_FOUND")]
    return checks, 0 if not bad else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Kiểm toán số liệu báo cáo/slide ↔ log thực nghiệm")
    ap.add_argument("--results", default=str(DEFAULT_RESULTS))
    ap.add_argument("--human", default=str(DEFAULT_HUMAN))
    ap.add_argument("--doc", action="append", default=[], help=".md/.txt/.docx/.pptx (lặp lại được)")
    ap.add_argument("--claims", default=None, help="File JSON danh sách claim")
    ap.add_argument("--no-mlflow", action="store_true")
    ap.add_argument("--render-table", action="store_true", help="In bảng Ablation chuẩn từ file log")
    ap.add_argument("--report", default=str(ROOT / "eval" / "results" / "verify_report.json"))
    args = ap.parse_args(argv)

    results = Path(args.results)
    if args.render_table:
        print(render_table(json.loads(results.read_text(encoding="utf-8"))["summary"]))
        return 0
    checks, code = run(results, Path(args.human), [Path(d) for d in args.doc], Path(args.claims) if args.claims else None, not args.no_mlflow)
    for c in checks:
        mark = {"PASS": "✔", "MISMATCH": "✘", "NOT_FOUND": "?", "UNPARSABLE": "?"}[c.status]
        print(f"{mark} {c.status:<10} {c.key:<40} claim={c.claimed:<10} {c.detail}  [{c.where}]")
    n_pass = sum(c.status == "PASS" for c in checks)
    print(f"\nTổng: {n_pass}/{len(checks)} PASS → " + ("PASS 100%" if code == 0 else ("KHÔNG CÓ CLAIM NÀO" if code == 2 else "FAIL")))
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(
        json.dumps({"exit_code": code, "checks": [asdict(c) for c in checks]}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
