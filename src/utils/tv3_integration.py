"""Explicit offline/LLM integration driver; no modifications to other members' graph.
A failed Critic is never silently treated as an approved plan.
"""
from functools import partial
import json
from pathlib import Path
from src.agents.profiler import ClassProfiler
from src.agents.outline_planner import OutlinePlanner
from src.agents.detail_designer import DetailDesigner
from src.utils.yccd_adapter import load_yccd
from src.utils.prompt_builder import dump, get
from src.utils.pedagogy_validator import PedagogyValidationError, validate_lesson_request, validate_outline
from src.utils.revision_engine import parse_instruction
from src.utils.tv3_bridge import normalize_chunks


def run_pipeline(request, chunks, taxonomy_path, *, approve=None, llm=None,
                 critic=None, scope_loader=None, max_reviews=3):
    """Use injected Retriever output and Critic; approval is an explicit callback.

    The caller owns code execution policy. With no critic, output is an unverified draft.
    Unstructured Critic feedback stops for human review rather than guessing patch targets.
    """
    validate_lesson_request(request)
    if type(max_reviews) is not int or not 1 <= max_reviews <= 3:
        raise ValueError('max_reviews must be 1–3')
    loader = partial(load_yccd, path=taxonomy_path)
    state = {'request': request, 'retrieved_chunks': normalize_chunks(chunks, get(request,'target_bai')), 'iteration': 0}
    state.update(ClassProfiler(taxonomy_path)(state))
    state.update(OutlinePlanner(llm, yccd_loader=loader)(state))
    if not get(request, 'bypass_hitl', False):
        if approve is None:
            return {**state, 'status': 'awaiting_approval'}
        approved = approve(state['outline'])
        validate_outline(approved, request)
        if get(approved, 'is_approved') is not True:
            return {**state, 'status': 'awaiting_approval'}
        state['outline'] = approved
    designer = DetailDesigner(llm, yccd_loader=loader, scope_loader=scope_loader)
    state.update(designer(state))
    if critic is None:
        return {**state, 'status': 'unverified_draft'}
    for index in range(max_reviews):
        state.update(critic(state))
        state['iteration'] = index + 1
        report = state.get('critic_report')
        if get(report, 'is_passed') is True:
            return {**state, 'status': 'critic_passed' if llm else 'offline_checks_passed'}
        if index + 1 == max_reviews or llm is None:
            break
        try:
            parse_instruction(get(report, 'revision_instructions', ''))
        except PedagogyValidationError:
            return {**state, 'status': 'needs_structured_feedback'}
        try:
            state.update(designer(state))
        except PedagogyValidationError:
            return {**state, 'status': 'revision_failed'}
    return {**state, 'status': 'needs_review'}


def export_verified(state, output_dir, *, allow_draft=False):
    from src.utils.docx_exporter import export_lesson_docx, export_student_docx
    status = state.get('status')
    # Offline pass is not evidence of semantic or human approval.
    if status != 'critic_passed' and not (allow_draft and status in {'offline_checks_passed','unverified_draft'}):
        raise PedagogyValidationError('Chưa đủ điều kiện xuất; cần kiểm định hoặc cho phép bản nháp')
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    prefix = 'draft_' if status != 'critic_passed' else ''
    teacher = root / (prefix + 'lesson_teacher.docx')
    student = root / (prefix + 'worksheet_student.docx')
    teacher.write_bytes(export_lesson_docx(state['request'],state['draft_plan']))
    student.write_bytes(export_student_docx(state['request'],state['draft_plan']))
    return teacher, student
