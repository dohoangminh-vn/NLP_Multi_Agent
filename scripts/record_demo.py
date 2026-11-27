"""Ghi lại 1 phiên chạy THẬT thành cache demo offline (TV5) – phòng mất mạng / rate-limit khi bảo vệ.

    LLM_PROVIDER=... LLM_MODEL=... python scripts/record_demo.py --bai 20 --mode 90m_full --name bai20_real

Phiên được chạy qua chính API (start → tự duyệt khung → resume), lưu mọi sự kiện SSE kèm độ trễ thật
vào data/demo_cache/<name>.json. Phát lại: GET /api/demo/stream?scenario=<name>.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bai", type=int, default=20)
    ap.add_argument("--mode", default="90m_full")
    ap.add_argument("--ratios", default="0.25,0.5,0.25")
    ap.add_argument("--name", default=None)
    args = ap.parse_args()

    from fastapi.testclient import TestClient

    from src.api.main import LLM_NAME, app
    from src.tools.ast_guard import SyllabusRules

    client = TestClient(app)
    b, s, a = (float(x) for x in args.ratios.split(","))
    body = {"target_bai": args.bai, "topic_name": SyllabusRules.load().lesson(args.bai)["title"], "scope_mode": args.mode,
            "basic_ratio": b, "standard_ratio": s, "advanced_ratio": a, "lab_constraints": "Phòng máy 1 HS/1 máy"}
    events, last = [], time.perf_counter()

    def consume(resp):
        nonlocal last
        buf = ""
        for text in resp.iter_text():
            buf += text
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                ev = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
                now = time.perf_counter()
                events.append({"event": ev["event"], "data": json.loads(ev["data"]), "delay_ms": int(1000 * (now - last))})
                last = now

    with client.stream("POST", "/api/lesson/start", json=body) as r:
        consume(r)
    hitl = next((e for e in events if e["event"] == "hitl_required"), None)
    if hitl:
        outline = {**hitl["data"]["outline"], "is_approved": True}
        events.append({"event": "_resume_marker", "data": {"outline": outline}, "delay_ms": 0})
        with client.stream("POST", "/api/lesson/resume", json={"thread_id": hitl["data"]["thread_id"], "outline": outline}) as r:
            consume(r)
    name = args.name or f"bai{args.bai}_{args.mode}"
    out = ROOT / "data" / "demo_cache" / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    reds = sum(1 for e in events if e["event"] == "critic" and e["data"].get("status") == "red")
    out.write_text(json.dumps({"_note": f"Phiên chạy thật ghi lúc {time.strftime('%Y-%m-%d %H:%M')} với LLM={LLM_NAME}",
                               "events": events}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(events)} sự kiện, {reds} thẻ đỏ → {out}")
    if reds == 0:
        print("Lưu ý: phiên này không có vòng AST/Sandbox bắt lỗi; nếu cần minh hoạ đỏ→xanh hãy ghi thêm bài khác (vd Bài 20, 22).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
