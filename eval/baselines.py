"""4 cấu hình Ablation – cùng 1 base LLM, temperature=0, bypass_hitl=True.

* B1 `ZeroShotSingleLLM`   : 1 lượt LLM, KHÔNG RAG.
* B2 `SingleAgentRAG`      : 1 lượt LLM + Hybrid RAG (retriever của TV2).
* B3 `MultiAgentSystem(critic=CriticConfig.llm_only())` : đồ thị Multi-Agent + LLM Critic thuần (tắt AST & Sandbox).
* B4 `MultiAgentSystem(critic=CriticConfig.full())`     : đồ thị Multi-Agent + AST Guard + Sandbox + Pedagogy Critic.

Hợp đồng với TV1 (src/graph/workflow.py) – hàm dựng đồ thị nhận các tham số (tuỳ chọn) sau:
    build_graph(llm=..., critic_node=..., retriever=..., checkpointer=None) -> CompiledGraph
    graph.invoke({"request": LessonRequest, "iteration": 0}, config) -> state có
        draft_plan, retrieved_chunks, critic_report, iteration
`MultiAgentSystem` tự lọc tham số theo chữ ký hàm, nên TV1 chỉ cần nhận `critic_node`.

Trước khi tích hợp (Tuần 2), `ReflectionLoopSystem` mô phỏng vòng Designer ↔ Critic (tối đa 3 vòng)
để chạy thử B3/B4. Số liệu CHÍNH THỨC phải chạy bằng đồ thị thật của TV1.
"""

from __future__ import annotations

import importlib
import inspect
import json
import uuid
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Protocol, Sequence

from src.agents.critic import CriticAgent, CriticConfig
from src.schemas import LessonPlan5512, LessonRequest, SourceCitation
from src.tools.ast_guard import _get, scope_summary
from src.utils.llm import LLMClient, extract_json

MAX_ITERATIONS = 3


@dataclass
class SystemOutput:
    plan: Optional[LessonPlan5512]
    retrieved_chunks: List[SourceCitation] = field(default_factory=list)
    iterations: int = 1
    critic_passed: Optional[bool] = None
    critic_history: List[dict] = field(default_factory=list)  # CriticReport mỗi vòng
    raw_text: str = ""
    error: str = ""


class LessonSystem(Protocol):
    name: str

    def generate(self, request: LessonRequest) -> SystemOutput: ...


class Retriever(Protocol):
    def retrieve(self, request: LessonRequest) -> List[SourceCitation]: ...


class FunctionRetriever:
    """Bọc 1 hàm `fn(request) -> list[SourceCitation|dict]` thành Retriever."""

    def __init__(self, fn: Callable):
        self.fn = fn

    def retrieve(self, request: LessonRequest) -> List[SourceCitation]:
        return [c if isinstance(c, SourceCitation) else SourceCitation.model_validate(c) for c in self.fn(request)]


# ----------------------------------------------------------------------------
# Prompt sinh giáo án 1 lượt (B1, B2 và Designer dự phòng)
# ----------------------------------------------------------------------------

GEN_SYSTEM = """Bạn là giáo viên Tin học THPT giỏi, soạn kế hoạch bài dạy theo Phụ lục IV – Công văn 5512 \
cho SGK Tin học 10 – Kết nối tri thức. Chỉ trả về MỘT object JSON hợp lệ đúng cấu trúc yêu cầu."""

_PLAN_SKELETON = {
    "knowledge_goals": "...",
    "general_competencies": "...",
    "cs_competencies": "NLa/NLb/NLc ...",
    "qualities": "...",
    "teaching_materials": "...",
    "activities": [
        {
            "activity_name": "Khởi động | Hình thành kiến thức | Luyện tập | Vận dụng",
            "duration_minutes": 0,
            "goal": "...",
            "content": "...",
            "expected_product": "...",
            "step1_transfer": "Chuyển giao nhiệm vụ",
            "step2_execute": "Thực hiện nhiệm vụ",
            "step3_report": "Báo cáo, thảo luận",
            "step4_conclude": "Kết luận, nhận định",
            "citations": [{"doc_name": "...", "bai_so": 0, "section_title": "...", "page_number": 0, "quoted_text": "..."}],
        }
    ],
    "worksheets": [
        {
            "tier": "Cơ bản | Đạt chuẩn | Nâng cao",
            "bloom_level": "Nhớ/Hiểu | Vận dụng | Phân tích/Sáng tạo",
            "prompt_text": "...",
            "starter_code": "...",
            "solution_code": "...",
            "test_cases": [{"stdin_data": "...", "expected_stdout": "...", "is_edge_case": False}],
        }
    ],
}


def build_generation_prompt(request: LessonRequest, chunks: Sequence[SourceCitation] = (), revision: str = "", previous: Optional[LessonPlan5512] = None) -> str:
    parts = [
        f"Soạn kế hoạch bài dạy Bài {request.target_bai} – {request.topic_name}, chế độ {request.scope_mode}, "
        f"TỔNG ĐÚNG {request.target_minutes} phút. Tỉ lệ lớp Cơ bản/Đạt chuẩn/Nâng cao = "
        f"{request.basic_ratio:.0%}/{request.standard_ratio:.0%}/{request.advanced_ratio:.0%}. "
        f"Điều kiện phòng máy: {request.lab_constraints or 'không ghi chú'}.",
        "Yêu cầu bắt buộc:",
        "- Đúng 4 hoạt động: Khởi động, Hình thành kiến thức, Luyện tập, Vận dụng; mỗi hoạt động đủ 4 bước tổ chức thực hiện.",
        "- Gắn citations (tên tài liệu, bài, mục, số trang, đoạn trích) cho nội dung lấy từ tài liệu.",
        "- Đúng 3 phiếu học tập: Cơ bản (Nhớ/Hiểu, điền khuyết ___), Đạt chuẩn (Vận dụng), Nâng cao (Phân tích/Sáng tạo, có trường hợp biên).",
        "- solution_code chạy được, dùng input() KHÔNG có prompt; mỗi phiếu có ít nhất 2 test_cases (stdin_data, expected_stdout).",
        "- Code chỉ dùng kiến thức đã học đến Bài " + str(request.target_bai) + ":",
        scope_summary(request.target_bai),
    ]
    if chunks:
        parts.append("\nTÀI LIỆU NGUỒN (chỉ dùng kiến thức trong đây, trích dẫn đúng trang):")
        for i, c in enumerate(chunks):
            parts.append(f"[{i + 1}] {c.doc_name} – Bài {c.bai_so} – {c.section_title} – tr.{c.page_number}: {c.quoted_text}")
    if previous is not None and revision:
        parts.append("\nBẢN NHÁP TRƯỚC (JSON):\n" + previous.model_dump_json())
        parts.append("\n" + revision)
    parts.append("\nTrả về JSON theo khung:\n" + json.dumps(_PLAN_SKELETON, ensure_ascii=False, indent=1))
    return "\n".join(parts)


def parse_plan(text: str) -> tuple[Optional[LessonPlan5512], str]:
    data = extract_json(text)
    if data is None:
        return None, "LLM không trả về JSON"
    try:
        return LessonPlan5512.model_validate(data), ""
    except Exception as exc:
        return None, f"JSON sai schema LessonPlan5512: {str(exc)[:300]}"


# ----------------------------------------------------------------------------
# B1, B2
# ----------------------------------------------------------------------------


class ZeroShotSingleLLM:
    name = "B1"

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def generate(self, request: LessonRequest) -> SystemOutput:
        text = self.llm.complete(GEN_SYSTEM, build_generation_prompt(request)).text
        plan, err = parse_plan(text)
        return SystemOutput(plan=plan, raw_text=text, error=err)


class SingleAgentRAG:
    name = "B2"

    def __init__(self, llm: LLMClient, retriever: Retriever):
        self.llm = llm
        self.retriever = retriever

    def generate(self, request: LessonRequest) -> SystemOutput:
        chunks = self.retriever.retrieve(request)
        text = self.llm.complete(GEN_SYSTEM, build_generation_prompt(request, chunks)).text
        plan, err = parse_plan(text)
        return SystemOutput(plan=plan, retrieved_chunks=chunks, raw_text=text, error=err)


# ----------------------------------------------------------------------------
# B3, B4 – đồ thị Multi-Agent
# ----------------------------------------------------------------------------


def load_object(spec: str):
    """'package.module:attr' → object."""
    module, _, attr = spec.partition(":")
    obj = importlib.import_module(module)
    for part in attr.split(".") if attr else []:
        obj = getattr(obj, part)
    return obj


def default_graph_factory():
    mod = importlib.import_module("src.graph.workflow")
    for name in ("build_graph", "build_workflow", "create_graph", "get_graph"):
        if hasattr(mod, name):
            return getattr(mod, name)
    raise ImportError("src.graph.workflow chưa có build_graph(...). Xem hợp đồng trong eval/baselines.py.")


def _call_with_supported_kwargs(fn: Callable, **kwargs):
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn(**kwargs)
    if any(p.kind == p.VAR_KEYWORD for p in params.values()):
        return fn(**kwargs)
    return fn(**{k: v for k, v in kwargs.items() if k in params})


class MultiAgentSystem:
    def __init__(self, name: str, llm: LLMClient, critic_config: CriticConfig, retriever: Optional[Retriever] = None,
                 graph_factory: Optional[Callable] = None, critic_llm: Optional[LLMClient] = None):
        self.name = name
        self.llm = llm
        self.critic_llm = critic_llm or llm
        self.critic_config = critic_config
        self.retriever = retriever
        self.graph_factory = graph_factory

    def generate(self, request: LessonRequest) -> SystemOutput:
        critic = CriticAgent(llm=self.critic_llm, config=self.critic_config)
        factory = self.graph_factory or default_graph_factory()
        graph = _call_with_supported_kwargs(factory, llm=self.llm, critic_node=critic, retriever=self.retriever, checkpointer=None)
        req = request.model_copy(update={"bypass_hitl": True})
        state = graph.invoke(
            {"request": req, "iteration": 0},
            config={"configurable": {"thread_id": f"bench-{uuid.uuid4().hex[:8]}"}, "recursion_limit": 50},
        )
        plan = _get(state, "draft_plan")
        if isinstance(plan, dict):
            plan = LessonPlan5512.model_validate(plan)
        chunks = [c if isinstance(c, SourceCitation) else SourceCitation.model_validate(c) for c in (_get(state, "retrieved_chunks", []) or [])]
        history = [h.report.model_dump() for h in critic.history]
        return SystemOutput(
            plan=plan,
            retrieved_chunks=chunks,
            iterations=max(1, len(critic.history)),
            critic_passed=critic.history[-1].report.is_passed if critic.history else None,
            critic_history=history,
            error="" if plan is not None else "Đồ thị không trả về draft_plan",
        )


class ReflectionLoopSystem:
    """Mô phỏng vòng Designer ↔ Critic (dùng khi chưa có đồ thị TV1)."""

    def __init__(self, name: str, llm: LLMClient, critic_config: CriticConfig, retriever: Optional[Retriever] = None,
                 max_iterations: int = MAX_ITERATIONS, critic_llm: Optional[LLMClient] = None):
        self.name = name
        self.llm = llm
        self.critic_llm = critic_llm or llm
        self.critic_config = critic_config
        self.retriever = retriever
        self.max_iterations = max_iterations

    def generate(self, request: LessonRequest) -> SystemOutput:
        critic = CriticAgent(llm=self.critic_llm, config=self.critic_config)
        chunks = self.retriever.retrieve(request) if self.retriever else []
        plan: Optional[LessonPlan5512] = None
        revision, raw, err = "", "", ""
        for _ in range(self.max_iterations):
            raw = self.llm.complete(GEN_SYSTEM, build_generation_prompt(request, chunks, revision, plan)).text
            new_plan, err = parse_plan(raw)
            if new_plan is None:
                if plan is None:
                    revision = "Bản trước không phải JSON hợp lệ – trả lại đúng khung JSON."
                    continue
                break  # giữ bản hợp lệ gần nhất
            plan = new_plan
            report = critic.review(request, plan, chunks).report
            if report.is_passed:
                break
            revision = report.revision_instructions
        return SystemOutput(
            plan=plan,
            retrieved_chunks=chunks,
            iterations=max(1, len(critic.history)),
            critic_passed=critic.history[-1].report.is_passed if critic.history else None,
            critic_history=[h.report.model_dump() for h in critic.history],
            raw_text=raw,
            error="" if plan is not None else err,
        )


def build_systems(configs: Sequence[str], llm: LLMClient, retriever: Optional[Retriever] = None,
                  graph_factory: Optional[Callable] = None, use_fallback_loop: bool = False,
                  critic_llm: Optional[LLMClient] = None) -> dict:
    systems = {}
    for cfg in configs:
        if cfg == "B1":
            systems[cfg] = ZeroShotSingleLLM(llm)
        elif cfg == "B2":
            if retriever is None:
                raise ValueError("B2 cần retriever (--retriever).")
            systems[cfg] = SingleAgentRAG(llm, retriever)
        elif cfg in ("B3", "B4"):
            cc = CriticConfig.llm_only() if cfg == "B3" else CriticConfig.full()
            if use_fallback_loop:
                systems[cfg] = ReflectionLoopSystem(cfg, llm, cc, retriever, critic_llm=critic_llm)
            else:
                systems[cfg] = MultiAgentSystem(cfg, llm, cc, retriever, graph_factory, critic_llm=critic_llm)
        else:
            raise ValueError(f"Cấu hình không hợp lệ: {cfg}")
    return systems
