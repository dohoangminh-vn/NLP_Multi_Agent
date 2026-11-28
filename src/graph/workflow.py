"""StateGraph Multi-Agent (TV1): 6 node + điểm dừng HITL + vòng phản tỉnh Designer ↔ Critic.

    START ─┬─ retriever ─┐
           └─ profiler ──┴─► outline_planner ─► (bypass_hitl?) ─┬─► detail_designer ◄──┐
                                               └─► human_review ┘        │           │ chưa đạt &
                                                  (interrupt_before)      ▼           │ iteration < 3
                                                                       critic ───────┘
                                                                         │ đạt / iteration == 3
                                                                         ▼
                                                                       export ─► END

HITL: đồ thị compile với `interrupt_before=["human_review"]`. API resume bằng
`graph.update_state(config, {"outline": edited})` rồi `graph.invoke(None, config)` (xem `resume_with_outline`).
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Callable, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from src.agents.critic import CriticAgent
from src.agents.detail_designer import DetailDesigner
from src.agents.outline_planner import OutlinePlanner
from src.agents.profiler import ClassProfiler
from src.schemas import CriticReport, LessonOutline, LessonPlan5512, LessonRequest, SourceCitation
from src.tools.ast_guard import _get

MAX_ITERATIONS = 3

NODE_LABELS = {
    "retriever": "Curriculum Retriever",
    "profiler": "Class Profiler",
    "outline_planner": "Outline Planner",
    "human_review": "HITL – Giáo viên duyệt khung",
    "detail_designer": "Detail & Worksheet Designer",
    "critic": "AST + Sandbox + Critic",
    "export": "Export",
}


class GraphState(TypedDict, total=False):
    request: LessonRequest
    retrieved_chunks: List[SourceCitation]
    retrieval_meta: str
    bloom_matrix_json: str
    outline: LessonOutline
    draft_plan: Optional[LessonPlan5512]
    critic_report: Optional[CriticReport]
    iteration: int
    status: str
    trace: Annotated[List[dict], operator.add]  # nhật kí critic từng vòng (cho Live Trace)


def _wrap_retriever(retriever) -> Callable:
    if retriever is None:
        from src.agents.retriever import build_benchmark_retriever

        retriever = build_benchmark_retriever()

    def node(state):
        req = _get(state, "request")
        req = LessonRequest.model_validate(req) if isinstance(req, dict) else req
        chunks = retriever.retrieve(req)
        meta = getattr(retriever, "last_meta", {}) or {}
        import json

        return {"retrieved_chunks": list(chunks), "retrieval_meta": json.dumps(meta, ensure_ascii=False)}

    return node


def _human_review(state):
    outline = _get(state, "outline")
    outline = LessonOutline.model_validate(outline) if isinstance(outline, dict) else outline
    return {"outline": outline.model_copy(update={"is_approved": True}), "status": "running"}


def _route_after_outline(state) -> str:
    req = _get(state, "request")
    return "detail_designer" if _get(req, "bypass_hitl", False) else "human_review"


def _make_critic_node(critic: Callable, max_iterations: int):
    def node(state):
        update = dict(critic(state))
        hist = getattr(critic, "history", None)
        if hist:
            update["trace"] = [{**hist[-1].to_trace_event(), "iteration": update.get("iteration", 0)}]
        return update

    return node


def _route_after_critic(max_iterations: int):
    def route(state) -> str:
        rep = _get(state, "critic_report")
        it = int(_get(state, "iteration", 0) or 0)
        if (rep is not None and _get(rep, "is_passed", False)) or it >= max_iterations:
            return "export"
        return "detail_designer"

    return route


def _export(state):
    rep = _get(state, "critic_report")
    return {"status": "done" if rep is not None and _get(rep, "is_passed", False) else "done_with_warnings"}


def build_graph(llm=None, critic_node: Optional[Callable] = None, retriever: Any = None, checkpointer: Any = "memory",
                designer: Optional[Callable] = None, outline_planner: Optional[Callable] = None,
                profiler: Optional[Callable] = None, max_iterations: int = MAX_ITERATIONS, hitl: bool = True):
    """Dựng & compile đồ thị.

    llm=None → chế độ offline (Outline/Designer tất định, Critic chỉ kiểm định tất định).
    checkpointer: "memory" (mặc định, MemorySaver) | None (benchmark, không lưu) | object checkpointer bất kỳ.
    """
    g = StateGraph(GraphState)
    g.add_node("retriever", _wrap_retriever(retriever))
    g.add_node("profiler", profiler or ClassProfiler())
    g.add_node("outline_planner", outline_planner or OutlinePlanner(llm))
    g.add_node("human_review", _human_review)
    g.add_node("detail_designer", designer or DetailDesigner(llm))
    g.add_node("critic", _make_critic_node(critic_node or CriticAgent(llm), max_iterations))
    g.add_node("export", _export)

    g.add_edge(START, "retriever")
    g.add_edge(START, "profiler")
    g.add_edge(["retriever", "profiler"], "outline_planner")
    g.add_conditional_edges("outline_planner", _route_after_outline, {"human_review": "human_review", "detail_designer": "detail_designer"})
    g.add_edge("human_review", "detail_designer")
    g.add_edge("detail_designer", "critic")
    g.add_conditional_edges("critic", _route_after_critic(max_iterations), {"export": "export", "detail_designer": "detail_designer"})
    g.add_edge("export", END)

    if checkpointer == "memory":
        from langgraph.checkpoint.memory import MemorySaver

        checkpointer = MemorySaver(serde=make_serde())
    return g.compile(checkpointer=checkpointer, interrupt_before=["human_review"] if (hitl and checkpointer is not None) else None)


SCHEMA_TYPES = [("src.schemas", n) for n in ("LessonRequest", "SourceCitation", "LessonOutline", "Activity5512",
                                                "TestCase", "WorksheetItem", "LessonPlan5512", "CriticReport", "AgentState")]


def make_serde():
    """Chỉ cho phép deserialize đúng các model trong src/schemas.py (an toàn checkpoint)."""
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    return JsonPlusSerializer(allowed_msgpack_modules=SCHEMA_TYPES)


def sqlite_checkpointer(path: str = "data/checkpoints.sqlite"):  # pragma: no cover - cần langgraph-checkpoint-sqlite
    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver

    return SqliteSaver(sqlite3.connect(path, check_same_thread=False), serde=make_serde())


def thread_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}


def resume_with_outline(graph, thread_id: str, outline: LessonOutline):
    """GV sửa khung tại HITL → cập nhật state → chạy tiếp (trả về iterator stream các update)."""
    cfg = thread_config(thread_id)
    graph.update_state(cfg, {"outline": outline})
    return graph.stream(None, cfg, stream_mode="updates")
