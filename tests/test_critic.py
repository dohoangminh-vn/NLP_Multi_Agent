import json
from pathlib import Path

import pytest

from src.agents.critic import CriticAgent, CriticConfig, parse_llm_scores
from src.schemas import AgentState, LessonPlan5512, LessonRequest, SourceCitation
from src.utils.llm import ScriptedLLM

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def all_good(score=5, covered=("F10.B20.YC1", "F10.B20.YC2")):
    return json.dumps(
        {
            "faithfulness": {"score": score, "unsupported_claims": []},
            "citation_support": {"score": score, "bad_citations": []},
            "yccd": {"score": score, "covered": list(covered), "missing": []},
            "timing": {"score": score, "issues": []},
            "differentiation": {"score": score, "issues": []},
            "code_review": {"scope_violations": [], "runtime_issues": []},
            "revision_suggestions": [],
        },
        ensure_ascii=False,
    )


@pytest.fixture
def req():
    return LessonRequest.model_validate(load("request_bai20.json"))


@pytest.fixture
def chunks():
    return [SourceCitation.model_validate(c) for c in load("chunks_bai20.json")]


@pytest.fixture
def clean():
    return LessonPlan5512.model_validate(load("plan_bai20_clean.json"))


@pytest.fixture
def bad():
    return LessonPlan5512.model_validate(load("plan_bai20_violations.json"))


def yccd20(_bai):
    return [{"code": "F10.B20.YC1", "text": "Viết được lệnh for"}, {"code": "F10.B20.YC2", "text": "Dùng range()"}]


def test_full_critic_passes_clean_plan(req, clean, chunks):
    critic = CriticAgent(ScriptedLLM(all_good()), CriticConfig.full(), yccd_loader=yccd20)
    res = critic.review(req, clean, chunks)
    r = res.report
    assert r.is_passed, res.gate_failures
    assert r.ast_violations == [] and r.sandbox_failures == []
    assert r.total_minutes_checked == 45
    assert r.faithfulness_score == 1.0 and r.citation_precision == 1.0 and r.yccd_coverage == 1.0
    assert r.revision_instructions == ""
    assert res.to_trace_event()["status"] == "green"


def test_full_critic_catches_everything(req, bad, chunks):
    critic = CriticAgent(ScriptedLLM(all_good()), CriticConfig.full(), yccd_loader=yccd20)
    res = critic.review(req, bad, chunks)
    r = res.report
    assert not r.is_passed
    assert any("Phiếu Nâng cao – dòng 2: dùng `ListComp`" in m for m in r.ast_violations)
    assert any("`max()`" in m for m in r.ast_violations)
    assert any("Phiếu Đạt chuẩn" in m and "JoinedStr" in m for m in r.ast_violations)
    assert any(m.startswith("Phiếu Cơ bản – test #1") for m in r.sandbox_failures)
    assert r.total_minutes_checked == 50
    assert res.citations.leaks == 1
    txt = r.revision_instructions
    assert "REVISION MODE" in txt and "Gợi ý:" in txt and "50 phút ≠ 45 phút" in txt and "Bài 22 > Bài 20" in txt
    assert res.to_trace_event()["status"] == "red"


def _code_only_violation_plan(clean):
    """Thời lượng/trích dẫn đúng, chỉ Phiếu Nâng cao vượt Bài 20 nhưng VẪN chạy đúng test."""
    plan = clean.model_copy(deep=True)
    plan.worksheets[2].solution_code = "n = int(input())\nA = [int(input()) for i in range(n)]\nprint(max(A))"
    return plan


def test_llm_only_critic_misses_scope_violation_but_full_catches(req, clean, chunks):
    """Lập luận cốt lõi của B3 vs B4: LLM tự soát code có thể bỏ sót, AST Guard thì không."""
    plan = _code_only_violation_plan(clean)
    b3 = CriticAgent(ScriptedLLM(all_good()), CriticConfig.llm_only(), yccd_loader=yccd20).review(req, plan, chunks)
    b4 = CriticAgent(ScriptedLLM(all_good()), CriticConfig.full(), yccd_loader=yccd20).review(req, plan, chunks)
    assert b3.report.is_passed  # LLM Critic thuần "tưởng" đạt
    assert not b4.report.is_passed and b4.report.ast_violations


def test_llm_only_prompt_asks_llm_to_review_code(req, clean, chunks):
    llm = ScriptedLLM(all_good())
    CriticAgent(llm, CriticConfig.llm_only(), yccd_loader=yccd20).review(req, clean, chunks)
    prompt = llm.calls[0][1]
    assert "scope_violations" in prompt and "solution_code" in prompt
    llm2 = ScriptedLLM(all_good())
    CriticAgent(llm2, CriticConfig.full(), yccd_loader=yccd20).review(req, clean, chunks)
    assert "scope_violations" not in llm2.calls[0][1] and "solution_code" not in llm2.calls[0][1]


def test_llm_reported_code_issues_flow_into_report(req, clean, chunks):
    reply = json.loads(all_good())
    reply["code_review"] = {"scope_violations": ["Phiếu Nâng cao dòng 2 dùng ListComp"], "runtime_issues": ["Phiếu Cơ bản test 1 sai"]}
    res = CriticAgent(ScriptedLLM(json.dumps(reply)), CriticConfig.llm_only(), yccd_loader=yccd20).review(req, clean, chunks)
    assert res.report.ast_violations == ["[LLM] Phiếu Nâng cao dòng 2 dùng ListComp"]
    assert res.report.sandbox_failures == ["[LLM] Phiếu Cơ bản test 1 sai"]
    assert not res.report.is_passed


def test_low_llm_scores_fail_gate(req, clean, chunks):
    res = CriticAgent(ScriptedLLM(all_good(score=2, covered=["F10.B20.YC1"])), yccd_loader=yccd20).review(req, clean, chunks)
    assert not res.report.is_passed
    assert res.report.yccd_coverage == 0.5
    assert res.report.faithfulness_score == 0.25
    assert any(g.startswith("Faithfulness") for g in res.gate_failures)


def test_garbage_and_exception_from_llm(req, clean, chunks):
    res = CriticAgent(ScriptedLLM("xin lỗi tôi không biết"), yccd_loader=yccd20).review(req, clean, chunks)
    assert not res.report.is_passed and "LLM Critic không chấm được" in res.gate_failures

    class Boom:
        model_name = "boom"

        def complete(self, s, u):
            raise TimeoutError("rate limit")

    res = CriticAgent(Boom(), yccd_loader=yccd20).review(req, clean, chunks)
    assert not res.report.is_passed and "rate limit" in res.report.revision_instructions


def test_deterministic_only_mode(req, clean, chunks):
    res = CriticAgent(None, CriticConfig(use_llm=False)).review(req, clean, chunks)
    assert res.report.is_passed and res.llm is None


def test_langgraph_node_interface(req, clean, chunks):
    critic = CriticAgent(ScriptedLLM(all_good()), yccd_loader=yccd20)
    state = {"request": req.model_dump(), "draft_plan": clean.model_dump(), "retrieved_chunks": [c.model_dump() for c in chunks], "iteration": 1}
    upd = critic(state)
    assert upd["iteration"] == 2 and upd["critic_report"].is_passed
    upd2 = critic(AgentState(request=req, draft_plan=clean, retrieved_chunks=chunks, iteration=0))
    assert upd2["iteration"] == 1
    assert critic({"request": req, "draft_plan": None})["critic_report"].is_passed is False
    assert len(critic.history) == 2


def test_parse_llm_scores_with_fences():
    s = parse_llm_scores("Kết quả:\n```json\n" + all_good(4) + "\n```")
    assert s.ok and s.faithfulness == 0.75 and s.likert["faithfulness"] == 4
