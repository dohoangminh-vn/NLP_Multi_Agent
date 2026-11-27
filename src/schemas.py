"""Hợp đồng dữ liệu Pydantic dùng chung (Contract-First).

File này dựng ĐÚNG theo sơ đồ mục 2.3 của kế hoạch (`ke_hoach_multi_agent_giao_vien.md`).
Chủ sở hữu chính thức: TV1. TV4 tạo bản này để các module Guardrails/Critic/Eval
chạy được ngay; nếu TV1 chốt bản khác, chỉ cần giữ nguyên tên trường bên dưới.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

ScopeMode = Literal["45m_theory", "45m_practice", "90m_full"]


class LessonRequest(BaseModel):
    target_bai: int = Field(..., ge=16, le=29, description="Bài SGK Tin học 10 KNTT (16–29)")
    topic_name: str = ""
    scope_mode: ScopeMode = "45m_theory"
    target_minutes: int = Field(45, description="45 hoặc 90")
    basic_ratio: float = 0.3
    standard_ratio: float = 0.5
    advanced_ratio: float = 0.2
    lab_constraints: str = ""
    bypass_hitl: bool = False


class SourceCitation(BaseModel):
    doc_name: str
    bai_so: int
    section_title: str = ""
    page_number: int
    quoted_text: str = ""


class LessonOutline(BaseModel):
    objective_summary: str = ""
    activity_titles: List[str] = Field(default_factory=list)
    activity_minutes: List[int] = Field(default_factory=list)
    worksheet_goals: List[str] = Field(default_factory=list)
    is_approved: bool = False


class Activity5512(BaseModel):
    activity_name: str
    duration_minutes: int
    goal: str = ""
    content: str = ""
    expected_product: str = ""
    step1_transfer: str = ""
    step2_execute: str = ""
    step3_report: str = ""
    step4_conclude: str = ""
    citations: List[SourceCitation] = Field(default_factory=list)


class TestCase(BaseModel):
    __test__ = False  # để pytest không nhầm là class test

    stdin_data: str = ""
    expected_stdout: str = ""
    is_edge_case: bool = False


class WorksheetItem(BaseModel):
    tier: str = Field(..., description="Cơ bản | Đạt chuẩn | Nâng cao")
    bloom_level: str = ""
    prompt_text: str = ""
    starter_code: str = ""
    solution_code: str = ""
    test_cases: List[TestCase] = Field(default_factory=list)


class LessonPlan5512(BaseModel):
    knowledge_goals: str = ""
    general_competencies: str = ""
    cs_competencies: str = ""
    qualities: str = ""
    teaching_materials: str = ""
    activities: List[Activity5512] = Field(default_factory=list)
    worksheets: List[WorksheetItem] = Field(default_factory=list)


class CriticReport(BaseModel):
    is_passed: bool = False
    ast_violations: List[str] = Field(default_factory=list)
    sandbox_failures: List[str] = Field(default_factory=list)
    faithfulness_score: float = 0.0
    citation_precision: float = 0.0
    yccd_coverage: float = 0.0
    differentiation_score: float = 0.0
    total_minutes_checked: int = 0
    revision_instructions: str = ""


class AgentState(BaseModel):
    request: LessonRequest
    retrieved_chunks: List[SourceCitation] = Field(default_factory=list)
    bloom_matrix_json: str = ""
    outline: Optional[LessonOutline] = None
    draft_plan: Optional[LessonPlan5512] = None
    critic_report: Optional[CriticReport] = None
    iteration: int = 0
