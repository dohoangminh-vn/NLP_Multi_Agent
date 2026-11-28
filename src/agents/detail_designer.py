"""TV3 detail generation, validated against the approved outline and sent evidence.

Offline plans are demonstration drafts. Revision requires explicit patch targets.
"""
from __future__ import annotations
import copy
import json
import logging
from src.agents.profiler import build_profile
from src.utils.pedagogy_validator import PedagogyValidationError
from src.utils.prompt_builder import build_detail_prompt, get, dump, TIERS, select_examples
from src.utils.yccd_adapter import load_yccd
from src.utils.revision_engine import parse_instruction, apply_patch

LOG = logging.getLogger(__name__)
HEADINGS = ('knowledge_goals', 'general_competencies', 'cs_competencies', 'qualities', 'teaching_materials')
ACT_FIELDS = ('goal', 'content', 'expected_product', 'step1_transfer', 'step2_execute', 'step3_report', 'step4_conclude')
BLOOM = ('Nhớ/Hiểu', 'Vận dụng', 'Phân tích/Sáng tạo')


def require(condition, message):
    if not condition:
        raise PedagogyValidationError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def validate_plan(plan, payload):
    """Check raw JSON before schema coercion. No claim about semantic correctness."""
    outline = payload['approved_outline']
    require(isinstance(plan, dict), 'Giáo án phải là JSON object')
    require(set(plan) == set(HEADINGS) | {'activities','worksheets'}, 'Sai trường giáo án')
    require(all(nonempty(plan.get(k)) for k in HEADINGS), 'Thiếu mục tiêu hoặc học liệu')
    require(plan['knowledge_goals'] == outline['objective_summary'], 'Mục tiêu không khớp khung')
    acts = plan['activities']
    require(isinstance(acts,list) and len(acts)==4, 'Cần đúng 4 hoạt động')
    for i, act in enumerate(acts):
        require(isinstance(act,dict) and set(act)==set(ACT_FIELDS)|{'activity_name','duration_minutes','citations'}, 'Sai trường hoạt động')
        require(act['activity_name']==outline['activity_titles'][i], 'Tên hoạt động khác khung')
        require(type(act['duration_minutes']) is int and act['duration_minutes']==outline['activity_minutes'][i], 'Thời lượng khác khung')
        require(all(nonempty(act.get(k)) for k in ACT_FIELDS), 'Thiếu nội dung hoặc bước tổ chức')
        citations = act['citations']
        require(isinstance(citations,list) and citations, 'Thiếu minh chứng hoạt động')
        require(all(isinstance(c,dict) and c in payload['retrieved_chunks'] for c in citations), 'Trích dẫn không thuộc nguồn đã gửi')
    worksheets = plan['worksheets']
    require(isinstance(worksheets,list) and len(worksheets)==3, 'Cần đúng 3 phiếu')
    for i, w in enumerate(worksheets):
        require(isinstance(w,dict) and set(w)=={'tier','bloom_level','prompt_text','starter_code','solution_code','test_cases'}, 'Sai trường phiếu')
        require(w['tier']==TIERS[i] and w['bloom_level']==BLOOM[i], 'Sai nhóm hoặc Bloom')
        require(nonempty(w['prompt_text']) and nonempty(w['solution_code']) and isinstance(w['starter_code'],str), 'Thiếu đề hoặc lời giải')
        require(w['starter_code'].strip()!=w['solution_code'].strip(), 'Starter trùng lời giải')
        require(isinstance(w['test_cases'],list) and len(w['test_cases'])>=2, 'Cần ít nhất 2 test case')
        for t in w['test_cases']:
            require(isinstance(t,dict) and set(t)=={'stdin_data','expected_stdout','is_edge_case'}, 'Sai trường test case')
            require(isinstance(t['stdin_data'],str) and isinstance(t['expected_stdout'],str) and type(t['is_edge_case']) is bool, 'Sai kiểu test case')
    return plan


def as_plan(data):
    try:
        from src.schemas import LessonPlan5512
    except ModuleNotFoundError as exc:
        if exc.name != 'src.schemas':
            raise
        return data
    return LessonPlan5512.model_validate(data)


def bank_worksheets(bai):
    """Legacy public reader retained for TV4 tests and consumers; same selected bank."""
    rows = copy.deepcopy(select_examples(bai))
    try:
        from src.schemas import WorksheetItem
    except ModuleNotFoundError as exc:
        if exc.name != 'src.schemas':
            raise
        return rows
    return [WorksheetItem.model_validate(row) for row in rows]


def offline_draft(payload):
    outline = payload['approved_outline']
    examples = payload['few_shot_examples']
    require(len(examples)==3, 'Offline thiếu ngân hàng phiếu đúng bài; cần LLM hoặc học liệu đã duyệt')
    profile = payload['profile']
    codes = sorted({c for y in payload['yccd'] for c in (y.get('competency_codes') or [])})
    acts = []
    sources = payload['retrieved_chunks']
    lab = '; '.join(profile.get('lab_notes', [])) or 'GV xác nhận thiết bị trước khi tổ chức.'
    for i, (title, minutes) in enumerate(zip(outline['activity_titles'], outline['activity_minutes'])):
        # Only evidence actually sent; never substitute a different lesson.
        c = sources[min(i, len(sources)-1)]
        acts.append(dict(activity_name=title,duration_minutes=minutes,goal=title,
            content=c['quoted_text'],expected_product='Bản nháp: câu trả lời hoặc bài làm theo nhiệm vụ đã giao.',
            step1_transfer='GV giao nhiệm vụ theo khung: '+title,
            step2_execute='HS đọc học liệu, thực hiện nhiệm vụ; GV hỗ trợ. '+lab,
            step3_report='HS trình bày kết quả; bạn học đối chiếu với học liệu.',
            step4_conclude='GV nhận xét theo mục tiêu khung; xác nhận kết quả trước khi chuyển hoạt động.',citations=[copy.deepcopy(c)]))
    return dict(knowledge_goals=outline['objective_summary'],general_competencies='Tự chủ và tự học; giải quyết vấn đề; GV xác nhận theo hoạt động.',
        cs_competencies=', '.join(codes) or 'Chưa có mã năng lực được xác nhận; GV cần đối chiếu.',
        qualities='Chăm chỉ, trách nhiệm khi thực hiện nhiệm vụ.',teaching_materials='Học liệu truy hồi và phiếu học tập mẫu. '+lab,
        activities=acts,worksheets=copy.deepcopy(examples))


class DetailDesigner:
    def __init__(self,llm=None,yccd_loader=load_yccd,max_json_retries=1,*,scope_loader=None):
        require(type(max_json_retries) is int and 0<=max_json_retries<=3,'Số lần thử lại phải từ 0 đến 3')
        self.llm=llm
        self.yccd_loader=yccd_loader
        self.max_json_retries=max_json_retries
        self.scope_loader=scope_loader

    def design(self,req,outline,profile,chunks,previous=None,revision='',iteration=0):
        if revision:
            require(previous is not None, 'Revision: thiếu bản nháp trước đó')
            instruction = parse_instruction(revision)
            require(self.llm is not None, 'Revision: cần LLM')
        elif previous is not None:
            raise PedagogyValidationError('Revision: thiếu mục tiêu sửa có cấu trúc')
        bai=get(req,'target_bai')
        scope=self.scope_loader(bai) if self.scope_loader else None
        system,user=build_detail_prompt(req,outline,profile,chunks,self.yccd_loader(bai),scope_rules=scope)
        system += '\nBắt buộc knowledge_goals bằng nguyên văn objective_summary; activity_name bằng nguyên văn từng activity_titles. Mỗi hoạt động có ít nhất một citation đúng nguồn; mỗi phiếu có ít nhất hai test case. Dùng đúng nhãn Bloom Nhớ/Hiểu, Vận dụng, Phân tích/Sáng tạo.'
        payload=json.loads(user)
        if revision:
            prior = dump(previous)
            system += '\nREVISION: chỉ trả JSON {"replacements": {"đường_dẫn": giá_trị_mới}}. Chỉ sửa các đường dẫn được cấp quyền. Không sửa test để che lỗi chương trình.'
            revision_user = json.dumps({'context':payload,'previous':prior,'revision':instruction},ensure_ascii=False)
            for _ in range(self.max_json_retries+1):
                try:
                    raw = self.llm.complete(system,revision_user).text.strip()
                    if raw.startswith('```json') and raw.endswith('```'):
                        raw = raw[7:-3].strip()
                    plan = apply_patch(prior,instruction['targets'],json.loads(raw))
                    return as_plan(validate_plan(plan,payload))
                except (ValueError,TypeError,AttributeError,KeyError,IndexError) as exc:
                    LOG.warning('Revision invalid: %s',type(exc).__name__)
            raise PedagogyValidationError('Revision: không có bản sửa hợp lệ')
        if self.llm is None:
            LOG.warning('Detail Designer offline: draft demonstration only')
            return as_plan(validate_plan(offline_draft(payload),payload))
        feedback=''
        for _ in range(self.max_json_retries+1):
            try:
                raw=self.llm.complete(system,user+feedback).text.strip()
                if raw.startswith('```json') and raw.endswith('```'):
                    raw=raw[7:-3].strip()
                plan=json.loads(raw)
                return as_plan(validate_plan(plan,payload))
            except (ValueError,TypeError,AttributeError) as exc:
                LOG.warning('Detail output invalid: %s',type(exc).__name__)
                feedback='\nKết quả trước sai cấu trúc hoặc ràng buộc. Kiểm tra lại toàn bộ hợp đồng; chỉ trả JSON.'
        raise PedagogyValidationError('LLM không trả giáo án hợp lệ sau số lần thử cho phép')

    def offline_plan(self,req,outline,profile,chunks):
        """Legacy entry point; retains the same approval and source validation."""
        return DetailDesigner(None,yccd_loader=self.yccd_loader,scope_loader=self.scope_loader).design(req,outline,profile,chunks)

    def __call__(self,state):
        req=get(state,'request')
        raw=get(state,'bloom_matrix_json','')
        profile=json.loads(raw) if raw else build_profile(req)
        report=get(state,'critic_report')
        revision=get(report,'revision_instructions','') if report and not get(report,'is_passed',False) else ''
        return {'draft_plan':self.design(req,get(state,'outline'),profile,get(state,'retrieved_chunks',[]) or [],get(state,'draft_plan'),revision,get(state,'iteration',0))}
