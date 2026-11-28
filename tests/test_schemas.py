"""Hợp đồng schema (TV1) + Profiler / Outline Planner / Designer (TV3)."""

import json

import pytest
from pydantic import ValidationError

from src.agents.detail_designer import DetailDesigner, bank_worksheets
from src.agents.outline_planner import OutlinePlanner
from src.agents.profiler import ClassProfiler, allocate_minutes, build_profile
from src.schemas import AgentState, LessonOutline, LessonPlan5512, LessonRequest
from src.utils.llm import ScriptedLLM


def test_schema_contract():
    with pytest.raises(ValidationError):
        LessonRequest(target_bai=15)
    with pytest.raises(ValidationError):
        LessonRequest(target_bai=20, scope_mode="60m")
    s = AgentState(request=LessonRequest(target_bai=20))
    assert s.iteration == 0 and s.draft_plan is None
    assert set(LessonPlan5512.model_fields) == {"knowledge_goals", "general_competencies", "cs_competencies", "qualities",
                                                "teaching_materials", "activities", "worksheets"}


@pytest.mark.parametrize("mode,target", [("45m_theory", 45), ("45m_practice", 45), ("90m_full", 90)])
@pytest.mark.parametrize("ratios", [(0.3, 0.5, 0.2), (0.5, 0.4, 0.1), (0.15, 0.45, 0.4)])
def test_minutes_always_sum_to_target(mode, target, ratios):
    req = LessonRequest(target_bai=20, scope_mode=mode, target_minutes=target, basic_ratio=ratios[0], standard_ratio=ratios[1], advanced_ratio=ratios[2])
    m = allocate_minutes(req)
    assert sum(m) == target and len(m) == 4 and min(m) >= 5


def test_profile_shifts_time_by_class():
    weak = allocate_minutes(LessonRequest(target_bai=20, basic_ratio=0.5, standard_ratio=0.4, advanced_ratio=0.1))
    strong = allocate_minutes(LessonRequest(target_bai=20, basic_ratio=0.15, standard_ratio=0.45, advanced_ratio=0.4))
    assert weak[1] > strong[1] and weak[3] < strong[3]
    p = build_profile(LessonRequest(target_bai=20, lab_constraints="Phòng máy 2 HS/1 máy"))
    assert [t["tier"] for t in p["tiers"]] == ["Cơ bản", "Đạt chuẩn", "Nâng cao"] and p["lab_notes"]
    assert "bloom_matrix_json" in ClassProfiler()({"request": LessonRequest(target_bai=20)})


def test_outline_llm_bad_minutes_falls_back():
    req = LessonRequest(target_bai=21, topic_name="Câu lệnh lặp while")
    bad = json.dumps({"activity_titles": ["A", "B", "C", "D"], "activity_minutes": [10, 10, 10, 10], "worksheet_goals": ["x", "y", "z"]})
    o = OutlinePlanner(ScriptedLLM(bad)).plan(req)
    assert o.activity_titles == ["A", "B", "C", "D"] and sum(o.activity_minutes) == 45
    assert OutlinePlanner(ScriptedLLM("không phải json")).plan(req).activity_titles[0].startswith("Khởi động")


def test_designer_llm_and_revision_mode():
    req = LessonRequest(target_bai=20, topic_name="Câu lệnh lặp for", scope_mode="45m_practice")
    outline = LessonOutline(activity_titles=["Khởi động", "Hình thành kiến thức", "Luyện tập", "Vận dụng"], activity_minutes=[5, 10, 20, 10])
    plan = DetailDesigner(None).offline_plan(req, outline, build_profile(req), [])
    wrong = plan.model_copy(deep=True)
    for a in wrong.activities:
        a.duration_minutes = 99
    llm = ScriptedLLM(["rác", wrong.model_dump_json()])
    d = DetailDesigner(llm)
    out = d.design(req, outline, build_profile(req), [], previous=plan, revision="REVISION MODE – sửa Phiếu Nâng cao", iteration=1)
    assert [a.duration_minutes for a in out.activities] == [5, 10, 20, 10]  # ép theo khung GV duyệt
    assert len(llm.calls) == 2 and "REVISION MODE" in llm.calls[0][1] and "LƯU Ý" in llm.calls[1][1]
    assert DetailDesigner(ScriptedLLM("rác")).design(req, outline, build_profile(req), [], previous=plan, revision="x") is plan


def test_bank_has_all_lessons():
    assert all(len(bank_worksheets(b)) == 3 for b in range(16, 30))
