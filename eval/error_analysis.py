"""Error Analysis tự động từ eval/results/benchmark_results.json → eval/results/error_analysis.md.

Trả lời trực tiếp câu hỏi phản biện số 2 của Hội đồng: lỗi nào LLM Critic thuần (B3) bỏ sót mà
AST Guard + Sandbox (B4) bắt được?

    python -m eval.error_analysis
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RESULTS = ROOT / "eval" / "results"


def build_report(payload: dict, max_examples: int = 8) -> str:
    cases = payload.get("cases", {})
    out = [f"# Error Analysis – Ablation ({payload.get('meta', {}).get('base_model', '?')})", "",
           "_Sinh tự động bởi `eval/error_analysis.py`. Mọi con số lấy từ `benchmark_results.json`._", ""]

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


def main(argv: Optional[Sequence[str]] = None) -> int:
    path = Path(argv[0]) if argv else RESULTS / "benchmark_results.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    md = build_report(payload)
    (path.parent / "error_analysis.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
