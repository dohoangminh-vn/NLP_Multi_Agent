"""FastAPI backend (TV5): SSE Live Trace, HITL resume, xuất DOCX, phát lại demo mock.

    uvicorn src.api.main:app --reload --port 8000        # mở http://localhost:8000

LLM: đặt LLM_PROVIDER + LLM_MODEL (xem src/utils/llm.py). Không đặt → chế độ OFFLINE
(Outline/Designer tất định từ SGK + ngân hàng phiếu; Critic chỉ kiểm định tất định).
Hợp đồng sự kiện: README.md, mục "Hợp đồng API & SSE".

Ghi cache 1 phiên chạy THẬT để demo offline (phòng mất mạng / rate-limit):
    LLM_PROVIDER=... LLM_MODEL=... python -m src.api.main record --bai 20 --mode 90m_full --name bai20_real
    → tests/mocks/demo_cache/bai20_real.json, phát lại: GET /api/demo/stream?scenario=bai20_real
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Iterator, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.graph.workflow import NODE_LABELS, build_graph, thread_config
from src.schemas import LessonOutline, LessonPlan5512, LessonRequest
from src.tools.ast_guard import ROOT, SyllabusRules
from src.tools.code_sandbox import verify_plan

MOCK_STREAM = ROOT / "tests" / "mocks" / "sse_stream_bai20.json"
MOCK_THREAD = "mock-bai20"
DEMO_CACHE = ROOT / "tests" / "mocks" / "demo_cache"
FRONTEND = ROOT / "frontend"
SCOPE_MODES = [
    {"value": "45m_theory", "label": "45p Lý thuyết", "minutes": 45},
    {"value": "45m_practice", "label": "45p Thực hành", "minutes": 45},
    {"value": "90m_full", "label": "90p Trọn bài", "minutes": 90},
]


def _make_llm():
    if not os.environ.get("LLM_MODEL"):
        return None, "offline"
    from src.utils.llm import llm_from_env

    llm = llm_from_env()
    return llm, llm.model_name


LLM, LLM_NAME = _make_llm()
GRAPH = build_graph(llm=LLM)
app = FastAPI(title="Multi-Agent Giáo án Tin học 10", version="1.0")


class ResumeBody(BaseModel):
    thread_id: str
    outline: LessonOutline


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _dump(v):
    return v.model_dump() if hasattr(v, "model_dump") else v


def _node_summary(node: str, upd: dict, state: dict) -> dict:
    if node == "retriever":
        chunks = [_dump(c) for c in upd.get("retrieved_chunks", [])]
        meta = json.loads(upd.get("retrieval_meta") or "{}")
        return {"n_chunks": len(chunks), "chunks": chunks, "rewrites": meta.get("rewrites", 0)}
    if node == "profiler":
        return {"profile": json.loads(upd.get("bloom_matrix_json") or "{}")}
    if node in ("outline_planner", "human_review"):
        return {"outline": _dump(upd.get("outline"))}
    if node == "detail_designer":
        plan = upd.get("draft_plan")
        it = int(state.get("iteration", 0) or 0)
        return {"iteration": it + 1, "n_activities": len(plan.activities) if plan else 0,
                "n_worksheets": len(plan.worksheets) if plan else 0, "revision": it > 0}
    if node == "critic":
        return (upd.get("trace") or [{}])[-1]
    if node == "export":
        return {"ready": True}
    return {}


def _final_payload(thread_id: str, values: dict) -> dict:
    plan: Optional[LessonPlan5512] = values.get("draft_plan")
    req: LessonRequest = values["request"]
    return {"thread_id": thread_id, "plan": _dump(plan), "critic_report": _dump(values.get("critic_report")),
            "verification": verify_plan(plan, req.target_bai) if plan else [], "iterations": values.get("iteration", 0)}


def _run(thread_id: str, inputs) -> Iterator[str]:
    cfg = thread_config(thread_id)
    running: set = set()  # node đã báo start nhưng chưa end → mỗi node luôn có đúng 1 cặp start/end

    def start(n):
        if n not in running and n != "__interrupt__":
            running.add(n)
            return sse("node_start", {"node": n, "label": NODE_LABELS.get(n, n)})
        return ""

    try:
        if inputs is not None:
            yield sse("session", {"thread_id": thread_id, "llm": LLM_NAME})
            for n in ("retriever", "profiler"):
                yield start(n)
        else:
            for n in GRAPH.get_state(cfg).next:
                yield start(n)
        for chunk in GRAPH.stream(inputs, cfg, stream_mode="updates"):
            for node, upd in chunk.items():
                if node == "__interrupt__":
                    continue
                upd = upd or {}
                state = GRAPH.get_state(cfg)
                summary = _node_summary(node, upd, state.values)
                yield start(node)
                if node == "critic":
                    yield sse("critic", summary)
                running.discard(node)
                yield sse("node_end", {"node": node, "label": NODE_LABELS.get(node, node), "summary": summary})
                for n in state.next:
                    # checkpoint có thể chưa chốt → `next` còn chứa chính node vừa xong: bỏ qua
                    if n not in ("human_review", node):
                        yield start(n)
        snap = GRAPH.get_state(cfg)
        if "human_review" in snap.next:
            v = snap.values
            yield sse("hitl_required", {"thread_id": thread_id, "outline": _dump(v.get("outline")),
                                        "target_minutes": v["request"].target_minutes,
                                        "profile": json.loads(v.get("bloom_matrix_json") or "{}")})
        elif not snap.next:
            yield sse("final", _final_payload(thread_id, snap.values))
            yield sse("done", {})
    except Exception as exc:  # luôn báo lỗi về UI thay vì cắt stream
        yield sse("error", {"message": f"{type(exc).__name__}: {exc}"})


def _stream(gen) -> StreamingResponse:
    return StreamingResponse(gen, media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/demo/list")
def demo_list():
    return {"scenarios": sorted(p.stem for p in DEMO_CACHE.glob("*.json")) if DEMO_CACHE.exists() else []}


@app.get("/api/health")
def health():
    return {"status": "ok", "llm": LLM_NAME}


@app.get("/api/meta")
def meta():
    rules = SyllabusRules.load()
    return {"lessons": [{"bai": l["bai"], "title": l["title"], "sgk_pages": l["sgk_pages"]} for l in rules.lessons], "scope_modes": SCOPE_MODES}


@app.post("/api/lesson/start")
def start(req: LessonRequest):
    total = req.basic_ratio + req.standard_ratio + req.advanced_ratio
    if abs(total - 1.0) > 0.011:
        raise HTTPException(422, f"Tổng tỉ lệ 3 nhóm phải = 100% (hiện {total:.0%})")
    req = req.model_copy(update={"target_minutes": 90 if req.scope_mode == "90m_full" else 45})
    thread_id = uuid.uuid4().hex[:12]
    return _stream(_run(thread_id, {"request": req, "iteration": 0, "trace": []}))


@app.post("/api/lesson/resume")
def resume(body: ResumeBody):
    cfg = thread_config(body.thread_id)
    snap = GRAPH.get_state(cfg)
    if not snap.values:
        raise HTTPException(404, "Không tìm thấy thread_id")
    if "human_review" not in snap.next:
        raise HTTPException(409, "Phiên này không ở trạng thái chờ duyệt")
    target = snap.values["request"].target_minutes
    if sum(body.outline.activity_minutes) != target:
        raise HTTPException(422, f"Tổng phút {sum(body.outline.activity_minutes)} ≠ {target}")
    GRAPH.update_state(cfg, {"outline": body.outline})
    return _stream(_run(body.thread_id, None))


@app.get("/api/lesson/{thread_id}")
def get_lesson(thread_id: str):
    snap = GRAPH.get_state(thread_config(thread_id))
    if not snap.values:
        raise HTTPException(404, "Không tìm thấy thread_id")
    v = snap.values
    status = "waiting_hitl" if "human_review" in snap.next else ("running" if snap.next else "done")
    plan = v.get("draft_plan")
    return {"status": status, "request": _dump(v.get("request")), "outline": _dump(v.get("outline")), "plan": _dump(plan),
            "critic_report": _dump(v.get("critic_report")), "iteration": v.get("iteration", 0),
            "verification": verify_plan(plan, v["request"].target_bai) if plan and status == "done" else []}


@app.get("/api/lesson/{thread_id}/export")
def export(thread_id: str):
    from src.utils.docx_exporter import export_lesson_docx

    if thread_id == MOCK_THREAD:  # nút tải Word sau khi chạy demo mock
        final = next(e["data"] for e in json.loads(MOCK_STREAM.read_text(encoding="utf-8"))["events"] if e["event"] == "final")
        req = LessonRequest.model_validate(json.loads((ROOT / "tests" / "mocks" / "request.json").read_text(encoding="utf-8")))
        plan = LessonPlan5512.model_validate(final["plan"])
    else:
        v = GRAPH.get_state(thread_config(thread_id)).values
        if not v or not v.get("draft_plan"):
            raise HTTPException(404, "Chưa có giáo án để xuất")
        req, plan = v["request"], v["draft_plan"]
    data = export_lesson_docx(req, plan, verify_plan(plan, req.target_bai))
    fname = f"KHBD_Bai{req.target_bai}_{req.scope_mode}.docx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.get("/api/demo/stream")
def demo_stream(speed: float = 1.0, scenario: str = ""):
    """Phát lại cache demo: `scenario` = tên file trong data/demo_cache/ (phiên chạy thật ghi bởi
    python -m src.api.main record); bỏ trống → luồng MOCK Bài 20 (đỏ → xanh) cho dựng UI."""
    path = MOCK_STREAM
    if scenario:
        path = DEMO_CACHE / f"{Path(scenario).name}.json"
        if not path.exists():
            raise HTTPException(404, f"Không có cache demo '{scenario}'")
    events = json.loads(path.read_text(encoding="utf-8"))["events"]

    def gen():
        for e in events:
            if e["event"].startswith("_"):
                continue
            time.sleep(max(0.0, e.get("delay_ms", 0) / 1000 / max(speed, 0.01)))
            yield sse(e["event"], e["data"])

    return _stream(gen())


if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")


# ----------------------------------------------------------------------------
# Ghi cache demo offline (TV5)

def record_demo(argv=None) -> int:
    """Ghi 1 phiên chạy THẬT qua API (start → tự duyệt khung → resume) thành cache demo offline."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--bai", type=int, default=20)
    ap.add_argument("--mode", default="90m_full")
    ap.add_argument("--ratios", default="0.25,0.5,0.25")
    ap.add_argument("--name", default=None)
    args = ap.parse_args(argv)

    from fastapi.testclient import TestClient

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
    out = DEMO_CACHE / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    reds = sum(1 for e in events if e["event"] == "critic" and e["data"].get("status") == "red")
    out.write_text(json.dumps({"_note": f"Phiên chạy thật ghi lúc {time.strftime('%Y-%m-%d %H:%M')} với LLM={LLM_NAME}",
                               "events": events}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(events)} sự kiện, {reds} thẻ đỏ → {out}")
    if reds == 0:
        print("Lưu ý: phiên này không có vòng AST/Sandbox bắt lỗi; nếu cần minh hoạ đỏ→xanh hãy ghi thêm bài khác (vd Bài 20, 22).")
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["record"]:
        raise SystemExit(record_demo(sys.argv[2:]))
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
