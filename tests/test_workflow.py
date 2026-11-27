"""Đồ thị LangGraph: HITL dừng/tiếp tục đúng state, bypass không treo, vòng Revision ≤ 3."""

import json
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from src.agents.critic import CriticAgent, CriticConfig  # noqa: E402
from src.graph.workflow import build_graph, resume_with_outline, thread_config  # noqa: E402
from src.schemas import LessonPlan5512, LessonRequest  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def req(bai=20, mode="45m_practice", bypass=False):
    return LessonRequest(target_bai=bai, topic_name="Câu lệnh lặp for", scope_mode=mode,
                         target_minutes=90 if mode == "90m_full" else 45, bypass_hitl=bypass)


def test_hitl_pause_and_resume_keeps_teacher_edits():
    g = build_graph(llm=None)
    cfg = thread_config("hitl")
    list(g.stream({"request": req(mode="90m_full"), "iteration": 0}, cfg))
    snap = g.get_state(cfg)
    assert snap.next == ("human_review",)
    assert "draft_plan" not in snap.values  # chưa sinh chi tiết trước khi GV duyệt
    edited = snap.values["outline"].model_copy(update={"activity_minutes": [8, 30, 34, 18],
                                                       "activity_titles": ["Khởi động: trò chơi đếm", "Hình thành kiến thức: for", "Luyện tập: 3 phiếu", "Vận dụng: tính tiền"]})
    list(resume_with_outline(g, "hitl", edited))
    v = g.get_state(cfg).values
    assert v["outline"].is_approved
    assert [a.duration_minutes for a in v["draft_plan"].activities] == [8, 30, 34, 18]
    assert v["status"] == "done" and v["critic_report"].is_passed


def test_bypass_runs_to_end_without_checkpointer():
    g = build_graph(llm=None, checkpointer=None)
    out = g.invoke({"request": req(bypass=True), "iteration": 0})
    assert out["status"] == "done" and out["iteration"] == 1
    assert all(c.bai_so <= 20 for c in out["retrieved_chunks"])


class FlakyDesigner:
    """Vòng 1 trả bản lỗi, các vòng sau trả bản sạch – kiểm tra Revision Mode nhận đúng chỉ dẫn."""

    def __init__(self, fail_times=1):
        self.calls, self.fail_times, self.revisions = 0, fail_times, []
        self.bad = LessonPlan5512.model_validate(json.loads((FIX / "plan_bai20_violations.json").read_text(encoding="utf-8")))
        self.good = LessonPlan5512.model_validate(json.loads((FIX / "plan_bai20_clean.json").read_text(encoding="utf-8")))

    def __call__(self, state):
        self.calls += 1
        rep = state.get("critic_report")
        self.revisions.append(rep.revision_instructions if rep is not None else "")
        return {"draft_plan": self.bad if self.calls <= self.fail_times else self.good}


def test_reflection_loop_revises_then_passes():
    d = FlakyDesigner(fail_times=1)
    g = build_graph(llm=None, designer=d, critic_node=CriticAgent(None, CriticConfig(use_llm=False)), checkpointer=None)
    out = g.invoke({"request": req(bypass=True), "iteration": 0, "trace": []})
    assert d.calls == 2 and out["iteration"] == 2 and out["critic_report"].is_passed
    assert "ListComp" in d.revisions[1] and "REVISION MODE" in d.revisions[1]
    assert [t["status"] for t in out["trace"]] == ["red", "green"]


def test_reflection_loop_stops_at_3():
    d = FlakyDesigner(fail_times=99)
    g = build_graph(llm=None, designer=d, critic_node=CriticAgent(None, CriticConfig(use_llm=False)), checkpointer=None)
    out = g.invoke({"request": req(bypass=True), "iteration": 0})
    assert d.calls == 3 and out["iteration"] == 3 and out["status"] == "done_with_warnings"


@pytest.mark.parametrize("bai", range(16, 30))
def test_offline_pipeline_every_lesson(bai):
    g = build_graph(llm=None, checkpointer=None)
    out = g.invoke({"request": LessonRequest(target_bai=bai, topic_name=f"Bài {bai}", bypass_hitl=True), "iteration": 0})
    r = out["critic_report"]
    assert r.is_passed, (r.ast_violations, r.sandbox_failures)
    assert r.total_minutes_checked == 45


def test_benchmark_multiagent_system_uses_real_graph():
    """eval.baselines.MultiAgentSystem (B3/B4) chạy trên build_graph thật: B3 sót lỗi, B4 bắt & sửa."""
    from eval.baselines import MultiAgentSystem
    from eval.evaluator import Evaluator
    from src.prompts import load_prompt
    from src.utils.llm import LLMResponse

    bad = json.loads((FIX / "plan_bai20_clean.json").read_text(encoding="utf-8"))
    bad["worksheets"][2]["solution_code"] = "n = int(input())\nA = [int(input()) for i in range(n)]\nprint(max(A))"
    good = json.loads((FIX / "plan_bai20_clean.json").read_text(encoding="utf-8"))

    class FakeLLM:
        model_name = "fake"

        def __init__(self):
            self.design_calls = 0

        def complete(self, system, user):
            if system == load_prompt("designer_system"):
                self.design_calls += 1
                return LLMResponse(json.dumps(bad if self.design_calls == 1 else good, ensure_ascii=False), 10, 10)
            if system == load_prompt("outline_system"):
                return LLMResponse("{}", 5, 5)
            yccd = ["F10.B20.YC1", "F10.B20.YC2"]
            reply = {k: {"score": 5} for k in ("faithfulness", "citation_support", "timing", "differentiation")}
            reply["yccd"] = {"score": 5, "covered": yccd}
            return LLMResponse(json.dumps(reply), 10, 10)

    from src.agents.critic import CriticConfig

    case = {"case_id": "c", "request": req(bypass=True).model_dump()}
    results = {}
    for name, cc in (("B3", CriticConfig.llm_only()), ("B4", CriticConfig.full())):
        out = MultiAgentSystem(name, FakeLLM(), cc, graph_factory=build_graph).generate(req(bypass=True))
        results[name] = Evaluator(judge_llm=None).evaluate(case, name, out)
        results[name].critic_passed = out.critic_passed
    assert results["B3"].critic_passed and results["B3"].n_oos_worksheets == 1  # LLM critic sót
    assert results["B4"].iterations == 2 and results["B4"].n_oos_worksheets == 0 and results["B4"].n_worksheets_code_pass == 3
