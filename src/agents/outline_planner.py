"""TV3 outline planner: validated offline baseline and optional LLM refinement."""
from __future__ import annotations
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from src.agents.profiler import ACTIVITIES, build_profile
from src.utils.pedagogy_validator import (PedagogyValidationError, validate_lesson_request, validate_outline)
from src.utils.yccd_adapter import load_yccd

LOG = logging.getLogger(__name__)
PROMPTS = Path(__file__).resolve().parents[1] / 'prompts'


def get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)


def as_outline(data):
    # TV1 owns this schema. Standalone tests can use the dictionary contract.
    try:
        from src.schemas import LessonOutline
    except ModuleNotFoundError as exc:
        if exc.name != 'src.schemas':
            raise
        return data
    return LessonOutline.model_validate(data)


def deterministic_outline(request, profile, yccd, rules=None):
    validate_lesson_request(request)
    bai = get(request, 'target_bai')
    if get(profile, 'target_bai') != bai or get(profile, 'scope_mode') != get(request, 'scope_mode') or get(profile, 'target_minutes') != get(request, 'target_minutes'):
        raise PedagogyValidationError('Hồ sơ lớp không khớp yêu cầu bài dạy')
    if not yccd:
        raise PedagogyValidationError(f'Không có YCCĐ bài {bai}')
    objectives = []
    for item in yccd:
        code, text = get(item, 'code'), get(item, 'text')
        if not isinstance(code, str) or not code.startswith(f'F10.B{bai}.YC') or not isinstance(text, str) or not text.strip():
            raise PedagogyValidationError('YCCĐ không hợp lệ hoặc khác bài')
        objectives.append(f'{code}: {text}')
    topic = get(request, 'topic_name') or f'Bài {bai}'
    practice = get(request, 'scope_mode') == '45m_practice'
    focus = ['Nêu tình huống và dự đoán kết quả', 'Củng cố kiến thức qua ví dụ' if practice else 'Khám phá kiến thức qua ví dụ', 'Thực hiện nhiệm vụ phân hóa và kiểm tra kết quả', 'Giải quyết tình huống mới trong phạm vi bài']
    data = dict(objective_summary='; '.join(objectives), activity_titles=[f'{a}: {f} – {topic}' for a, f in zip(ACTIVITIES, focus)], activity_minutes=[get(a, 'minutes') for a in get(profile, 'activities', [])], worksheet_goals=[f'Cơ bản (Nhớ/Hiểu): giải thích ví dụ, đọc hiểu hoặc hoàn thiện đoạn code về {topic}', f'Đạt chuẩn (Vận dụng): thực hiện nhiệm vụ lập trình theo YCCĐ về {topic}', f'Nâng cao (Phân tích/Sáng tạo): phân tích lỗi hoặc trường hợp biên trong phạm vi {topic}'], is_approved=False)
    validate_outline(data, request)
    return as_outline(data)


def dump(obj):
    return dict(obj) if isinstance(obj, Mapping) else obj.model_dump()


class OutlinePlanner:
    def __init__(self, llm=None, yccd_loader=load_yccd):
        self.llm = llm
        self.yccd_loader = yccd_loader

    def plan(self, request, profile=None, chunks=()):
        validate_lesson_request(request)
        profile = build_profile(request) if profile is None else profile
        yccd = self.yccd_loader(get(request, 'target_bai'))
        base = deterministic_outline(request, profile, yccd)
        if self.llm is None:
            return base
        data = dump(base)
        # Keep factual objectives, timing and approval under deterministic control.
        rules = '\n\n'.join((PROMPTS / name).read_text(encoding='utf-8-sig') for name in ('pedagogy_rules.md', 'bloom_rules.md', 'competency_rules.md', 'outline_prompt.txt'))
        context = [{k: get(c,k) for k in ('doc_name','bai_so','page_number','quoted_text')} for c in chunks if get(c,'bai_so') == get(request,'target_bai')][:6]
        payload = json.dumps({'baseline': data, 'profile': profile, 'yccd': yccd, 'retrieved_chunks': context}, ensure_ascii=False)
        try:
            raw = self.llm.complete(rules, payload).text.strip()
            if raw.startswith('```json') and raw.endswith('```'):
                raw = raw[7:-3].strip()
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError('Expected JSON object')
            titles, goals = result['activity_titles'], result['worksheet_goals']
            candidate = {**data, 'activity_titles': titles, 'worksheet_goals': goals}
            validate_outline(candidate, request)
            if any(not goal.strip().casefold().startswith(tier.casefold()) for goal,tier in zip(goals, ('Cơ bản','Đạt chuẩn','Nâng cao'))):
                raise PedagogyValidationError('Sai thứ tự phân hóa')
            return as_outline(candidate)
        except Exception as exc:
            LOG.warning('Outline LLM fallback: %s', type(exc).__name__)
            return base

    def __call__(self, state):
        request = get(state, 'request')
        raw = get(state, 'bloom_matrix_json', '')
        profile = json.loads(raw) if raw else None
        return {'outline': self.plan(request, profile, get(state, 'retrieved_chunks', []) or [])}
