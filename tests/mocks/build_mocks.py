"""Sinh tests/mocks/ (TV1) từ fixture + code thật → mock luôn khớp schema hiện hành.

    python tests/mocks/build_mocks.py

Gồm mock đầu ra từng node và 1 luồng SSE mẫu Bài 20 (vòng 1 thẻ đỏ AST/Sandbox → vòng 2 thẻ xanh)
để TV5 dựng UI khi chưa có backend thật. Đây là DỮ LIỆU MOCK, không phải kết quả chạy LLM.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.agents.critic import CriticAgent, CriticConfig  # noqa: E402
from src.agents.outline_planner import OutlinePlanner  # noqa: E402
from src.agents.profiler import build_profile  # noqa: E402
from src.schemas import LessonPlan5512, LessonRequest, SourceCitation  # noqa: E402
from src.tools.code_sandbox import verify_plan  # noqa: E402

MOCKS = ROOT / "tests" / "mocks"
FIX = MOCKS  # giáo án mẫu đầu vào (plan_bai20_*.json, request_bai20.json, chunks_bai20.json)


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def dump(name, obj):
    (MOCKS / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    MOCKS.mkdir(parents=True, exist_ok=True)
    req = LessonRequest.model_validate(load("request_bai20.json"))
    chunks = [SourceCitation.model_validate(c) for c in load("chunks_bai20.json")]
    draft1 = LessonPlan5512.model_validate(load("plan_bai20_violations.json"))
    draft2 = LessonPlan5512.model_validate(load("plan_bai20_clean.json"))
    profile = build_profile(req)
    outline = OutlinePlanner(llm=None).plan(req, profile, chunks)
    critic = CriticAgent(None, CriticConfig(use_llm=False))
    r1 = critic.review(req, draft1, chunks)
    r2 = critic.review(req, draft2, chunks)
    ev1 = {**r1.to_trace_event(), "iteration": 1}
    ev2 = {**r2.to_trace_event(), "iteration": 2}

    dump("request.json", req.model_dump())
    dump("retriever_output.json", {"retrieved_chunks": [c.model_dump() for c in chunks]})
    dump("profiler_output.json", {"bloom_matrix_json": json.dumps(profile, ensure_ascii=False)})
    dump("outline_output.json", {"outline": outline.model_dump()})
    dump("designer_draft1.json", {"draft_plan": draft1.model_dump(), "iteration": 0})
    dump("designer_draft2.json", {"draft_plan": draft2.model_dump(), "iteration": 1})
    dump("critic_round1.json", {"critic_report": r1.report.model_dump(), "iteration": 1})
    dump("critic_round2.json", {"critic_report": r2.report.model_dump(), "iteration": 2})

    tid = "mock-bai20"
    approved = outline.model_copy(update={"is_approved": True})
    stream = [
        {"event": "session", "data": {"thread_id": tid, "mock": True}, "delay_ms": 0},
        {"event": "node_start", "data": {"node": "retriever", "label": "Curriculum Retriever"}, "delay_ms": 100},
        {"event": "node_start", "data": {"node": "profiler", "label": "Class Profiler"}, "delay_ms": 50},
        {"event": "node_end", "data": {"node": "profiler", "label": "Class Profiler", "summary": {"profile": profile}}, "delay_ms": 300},
        {"event": "node_end", "data": {"node": "retriever", "label": "Curriculum Retriever",
                                       "summary": {"n_chunks": len(chunks), "chunks": [c.model_dump() for c in chunks], "rewrites": 0}}, "delay_ms": 700},
        {"event": "node_start", "data": {"node": "outline_planner", "label": "Outline Planner"}, "delay_ms": 100},
        {"event": "node_end", "data": {"node": "outline_planner", "label": "Outline Planner", "summary": {"outline": outline.model_dump()}}, "delay_ms": 600},
        {"event": "hitl_required", "data": {"thread_id": tid, "outline": outline.model_dump(), "target_minutes": req.target_minutes, "profile": profile}, "delay_ms": 200},
        {"event": "_resume_marker", "data": {"outline": approved.model_dump()}, "delay_ms": 0},
        {"event": "node_start", "data": {"node": "detail_designer", "label": "Detail & Worksheet Designer"}, "delay_ms": 100},
        {"event": "node_end", "data": {"node": "detail_designer", "label": "Detail & Worksheet Designer",
                                       "summary": {"iteration": 1, "n_activities": 4, "n_worksheets": 3, "revision": False}}, "delay_ms": 1200},
        {"event": "node_start", "data": {"node": "critic", "label": "AST + Sandbox + Critic"}, "delay_ms": 100},
        {"event": "critic", "data": ev1, "delay_ms": 800},
        {"event": "node_end", "data": {"node": "critic", "label": "AST + Sandbox + Critic", "summary": ev1}, "delay_ms": 50},
        {"event": "node_start", "data": {"node": "detail_designer", "label": "Detail & Worksheet Designer (Revision Mode)"}, "delay_ms": 100},
        {"event": "node_end", "data": {"node": "detail_designer", "label": "Detail & Worksheet Designer (Revision Mode)",
                                       "summary": {"iteration": 2, "n_activities": 4, "n_worksheets": 3, "revision": True}}, "delay_ms": 1200},
        {"event": "node_start", "data": {"node": "critic", "label": "AST + Sandbox + Critic"}, "delay_ms": 100},
        {"event": "critic", "data": ev2, "delay_ms": 800},
        {"event": "node_end", "data": {"node": "critic", "label": "AST + Sandbox + Critic", "summary": ev2}, "delay_ms": 50},
        {"event": "node_end", "data": {"node": "export", "label": "Export", "summary": {"ready": True}}, "delay_ms": 100},
        {"event": "final", "data": {"thread_id": tid, "plan": draft2.model_dump(), "critic_report": r2.report.model_dump(),
                                    "verification": verify_plan(draft2, req.target_bai), "iterations": 2}, "delay_ms": 100},
        {"event": "done", "data": {}, "delay_ms": 0},
    ]
    dump("sse_stream_bai20.json", {"_note": "MOCK – dựng từ fixture + Critic tất định, không phải kết quả LLM.", "events": stream})
    print(f"Đã ghi {len(list(MOCKS.glob('*.json')))} file mock vào {MOCKS}")


if __name__ == "__main__":
    main()
