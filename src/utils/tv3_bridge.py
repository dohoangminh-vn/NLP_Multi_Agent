"""TV3 adapters for team contracts. Does not mutate the Retriever or Critic."""
import json
from collections.abc import Mapping
from src.utils.prompt_builder import get, ground_context
from src.utils.pedagogy_validator import PedagogyValidationError, validate_lesson_number
from src.utils.revision_engine import parse_instruction


def to_citation(chunk):
    """Accept TV1 SourceCitation, TV2 DocumentChunk, TV4 flat Chunk or dict.

    Preserve actual evidence; never fill a missing page or invent a quote.
    """
    if callable(getattr(chunk, 'to_source_citation', None)):
        # Use the full chunk rather than the DTO's default 300-character excerpt.
        chunk = chunk.to_source_citation(quoted_text=get(chunk, 'content'))
    elif callable(getattr(chunk, 'to_citation', None)):
        chunk = chunk.to_citation()
    meta = get(chunk, 'metadata')
    if meta is not None:
        chunk = {k:get(meta,k) for k in ('doc_name','bai_so','page_number')} | {
            'section_title':get(meta,'muc'), 'quoted_text':get(chunk,'content')}
    elif get(chunk,'quoted_text') is None and get(chunk,'text') is not None:
        chunk = {k:get(chunk,k) for k in ('doc_name','bai_so','page_number')} | {
            'section_title':get(chunk,'muc'), 'quoted_text':get(chunk,'text')}
    row = {k:get(chunk,k) for k in ('doc_name','bai_so','section_title','page_number','quoted_text')}
    if (type(row['bai_so']) is not int or type(row['page_number']) is not int or row['page_number'] < 1
            or any(not isinstance(row[k],str) or not row[k].strip() for k in ('doc_name','section_title','quoted_text'))):
        raise PedagogyValidationError('Chunk thiếu metadata/trích dẫn hợp lệ')
    validate_lesson_number(row['bai_so'])
    return row


def normalize_chunks(chunks, bai):
    validate_lesson_number(bai)
    rows=[]
    for chunk in chunks:
        row=to_citation(chunk)
        if row['bai_so']==bai and row not in rows:
            rows.append(row)
    if not rows:
        raise PedagogyValidationError('Không có chunk đúng bài')
    return rows


def structured_feedback(result):
    """Translate only identifiable AST/Sandbox worksheet errors from TV4 history.

    Other gates (YCCĐ, citation, timing, semantic scores) require explicit targets
    from TV4/teacher. Never authorize changing expected outputs to hide a bug.
    """
    report=get(result,'report')
    if report is None or get(report,'is_passed') is True:
        return None
    gates=get(result,'gate_failures',[]) or []
    if not gates or any(not isinstance(g,str) or not g.startswith(('AST:', 'Sandbox:')) for g in gates):
        return None
    ast=get(result,'ast_results',[]) or []
    sandbox=get(result,'sandbox_results',[]) or []
    if any(len(group) not in (0,3) for group in (ast,sandbox)):
        return None
    if any(g.startswith('AST:') for g in gates) and not any(get(x,'passed') is False for x in ast):
        return None
    if any(g.startswith('Sandbox:') for g in gates) and not any(get(x,'passed') is False for x in sandbox):
        return None
    paths=[]
    for i in range(3):
        if ast and get(ast[i],'passed') is False:
            paths += [f'worksheets.{i}.starter_code',f'worksheets.{i}.solution_code']
        elif sandbox and get(sandbox[i],'passed') is False:
            paths += [f'worksheets.{i}.solution_code']
    if not paths:
        return None
    messages=(get(report,'ast_violations',[]) or []) + (get(report,'sandbox_failures',[]) or [])
    instructions='Sửa code đúng phạm vi và đặc tả. Giữ nguyên đề, test_cases và expected_stdout. Không sửa ngoài targets.\n'+'\n'.join(str(m) for m in messages)
    raw=json.dumps({'targets':paths,'instructions':instructions},ensure_ascii=False)
    parse_instruction(raw)
    return raw


class CriticBridge:
    """Opt-in wrapper for sequential driver. Preserve report metrics and history."""
    def __init__(self, critic):
        self.critic=critic

    def __call__(self,state):
        history=getattr(self.critic,'history',None)
        before=len(history) if history is not None else 0
        update=dict(self.critic(state))
        history=getattr(self.critic,'history',None)
        if history is None or len(history)!=before+1:
            return update
        raw=structured_feedback(history[-1])
        if raw is not None:
            rep=update['critic_report']
            update['critic_report'] = {**rep,'revision_instructions':raw} if isinstance(rep,Mapping) else rep.model_copy(update={'revision_instructions':raw})
        return update
