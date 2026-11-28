"""TV3 prompt construction; no model calls or shared schema dependency."""
from pathlib import Path
from collections.abc import Mapping
import json
from src.utils.pedagogy_validator import validate_outline, PedagogyValidationError

PROMPTS = Path(__file__).resolve().parents[1] / 'prompts'
TIERS = ('Cơ bản', 'Đạt chuẩn', 'Nâng cao')
RULES = ('pedagogy_rules.md', 'bloom_rules.md', 'competency_rules.md')


def get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)


def dump(obj):
    return dict(obj) if isinstance(obj, Mapping) else obj.model_dump(mode='json')


def select_examples(bai, root=PROMPTS):
    """Exact lesson only. Prefer shared bank, otherwise local reviewed example."""
    root = Path(root)
    bank = root / 'worksheet_bank.json'
    local = root / 'few_shot' / 'lesson_20.json'
    if bank.exists():
        entry = json.loads(bank.read_text(encoding='utf-8-sig'))['lessons'].get(str(bai))
    elif (root / 'few_shot' / 'tv3_worksheet_bank.json').exists():
        entry = json.loads((root / 'few_shot' / 'tv3_worksheet_bank.json').read_text(encoding='utf-8-sig'))['lessons'].get(str(bai))
    elif bai == 20 and local.exists():
        entry = json.loads(local.read_text(encoding='utf-8-sig'))
    else:
        return []
    if entry is None:
        return []
    rows = entry['worksheets']
    if [x['tier'] for x in rows] != list(TIERS):
        raise PedagogyValidationError('Few-shot sai thứ tự nhóm')
    return rows


def ground_context(chunks, bai, max_chars=12000):
    """Keep exact quotes and metadata; skip oversized chunks, never fabricate citations."""
    if type(max_chars) is not int or max_chars <= 0:
        raise ValueError('max_chars must be positive integer')
    out = []
    for c in chunks:
        if get(c, 'bai_so') != bai:
            continue
        row = {k: get(c,k) for k in ('doc_name','bai_so','section_title','page_number','quoted_text')}
        if not all(isinstance(row[k],str) and row[k].strip() for k in ('doc_name','quoted_text')) or type(row['page_number']) is not int or row['page_number'] < 1:
            continue
        if row in out:
            continue
        if len(json.dumps(out + [row], ensure_ascii=False)) <= max_chars:
            out.append(row)
    return out


def build_detail_prompt(request, outline, profile, chunks, yccd, *, root=PROMPTS, scope_rules=None, max_chars=12000):
    validate_outline(outline, request)
    if get(outline,'is_approved') is not True and get(request,'bypass_hitl') is not True:
        raise PedagogyValidationError('Chưa duyệt khung giáo án')
    bai = get(request,'target_bai')
    if any(get(profile,k) != get(request,k) for k in ('target_bai','scope_mode','target_minutes')):
        raise PedagogyValidationError('Hồ sơ lớp không khớp')
    if not yccd or any(not str(get(x,'code','')).startswith(f'F10.B{bai}.YC') or not str(get(x,'text','')).strip() for x in yccd):
        raise PedagogyValidationError('Thiếu hoặc sai YCCĐ')
    context = ground_context(chunks,bai,max_chars)
    if not context:
        raise PedagogyValidationError('Không có nguồn truy hồi hợp lệ cho bài')
    root = Path(root)
    system = '\n\n'.join((root/name).read_text(encoding='utf-8-sig') for name in (*RULES,'detail_prompt.txt','worksheet_prompt.txt'))
    payload = {'request':dump(request),'approved_outline':dump(outline),'profile':dump(profile),'yccd':[dump(x) for x in yccd], 'retrieved_chunks':context,'scope_rules':scope_rules,'few_shot_examples':select_examples(bai,root)}
    return system,json.dumps(payload,ensure_ascii=False,allow_nan=False)


def build_revision_prompt(previous, instructions, *, root=PROMPTS):
    if not isinstance(instructions,str) or not instructions.strip():
        raise PedagogyValidationError('Thiếu hướng dẫn sửa')
    return (Path(root)/'revision_prompt.txt').read_text(encoding='utf-8-sig'), json.dumps({'previous':dump(previous),'critic_instructions':instructions},ensure_ascii=False,allow_nan=False)
