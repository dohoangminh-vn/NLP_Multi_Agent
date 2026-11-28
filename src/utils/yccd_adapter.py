"""Read the two team taxonomy formats without conflating their provenance."""
from __future__ import annotations
import json
import re
from pathlib import Path
from src.utils.pedagogy_validator import PedagogyValidationError, validate_lesson_number

DEFAULT_TAXONOMY = Path(__file__).resolve().parents[2] / 'data' / 'yccd_taxonomy.json'
CODES = {'NLa', 'NLb', 'NLc', 'NLd', 'NLe'}
ID = re.compile(r'F10\.B(\d+)\.YC\d+\Z')


def _normalize(item, bai, legacy=False, code=None):
    if not isinstance(item, dict):
        raise PedagogyValidationError('YCCĐ phải là object')
    key = code if legacy else item.get('yccd_id')
    text = item.get('text' if legacy else 'description')
    match = ID.fullmatch(key) if isinstance(key, str) else None
    if not match or int(match[1]) != bai or not isinstance(text, str) or not text.strip():
        raise PedagogyValidationError('Mã hoặc nội dung YCCĐ không hợp lệ')
    codes = [] if legacy else item.get('competency_codes')
    levels = [] if legacy else item.get('bloom_levels', [])
    if not isinstance(codes, list) or (not legacy and not codes) or any(not isinstance(c,str) or c not in CODES for c in codes):
        raise PedagogyValidationError('Mã năng lực YCCĐ không hợp lệ')
    if not isinstance(levels,list) or any(not isinstance(x,str) or not x.strip() for x in levels):
        raise PedagogyValidationError('Bloom YCCĐ không hợp lệ')
    return {'yccd_id': key, 'description': text, 'competency_codes': list(codes),
            'bloom_levels': list(levels), 'code': key, 'text': text,
            'source_format': 'legacy_sgk' if legacy else 'hierarchical_tv2',
            'source': item.get('source'), 'ct2018_page': item.get('ct2018_page')}


def read_yccd(bai, path=DEFAULT_TAXONOMY):
    """Return (items, status). Missing data is explicit; malformed data fails."""
    validate_lesson_number(bai)
    if path is None or not Path(path).exists():
        return [], 'missing_taxonomy'
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, ValueError) as exc:
        raise PedagogyValidationError('Không đọc được taxonomy JSON') from exc
    if not isinstance(data, dict):
        return [], 'unsupported_taxonomy'
    if 'lessons' in data:
        lessons = data['lessons']
        if not isinstance(lessons,list) or any(not isinstance(x,dict) for x in lessons):
            raise PedagogyValidationError('Danh sách bài học không hợp lệ')
        matches = [x for x in lessons if x.get('bai_so') == bai]
        if len(matches) != 1:
            return [], 'lesson_not_unique_or_missing'
        items = matches[0].get('yccd_items', [])
        if not isinstance(items,list):
            raise PedagogyValidationError('yccd_items phải là danh sách')
        out = [_normalize(x,bai) for x in items]
        status = 'mapped_from_tv2'
    else:
        entries = [(k,v) for k,v in data.items() if ID.fullmatch(k)]
        if not entries:
            return [], 'unsupported_taxonomy'
        out = []
        for k,v in entries:
            if not isinstance(v,dict) or v.get('bai_so') != int(ID.fullmatch(k)[1]):
                raise PedagogyValidationError('Mã YCCĐ không khớp bài học')
            if v['bai_so'] == bai:
                out.append(_normalize(v,bai,True,k))
        status = 'legacy_sgk_unverified'
    if not out:
        return [], 'missing_yccd'
    if len({x['code'] for x in out}) != len(out):
        raise PedagogyValidationError('Mã YCCĐ trùng lặp')
    return out,status


def load_yccd(bai, path=DEFAULT_TAXONOMY):
    """Compatibility callable for OutlinePlanner; do not silently use missing YCCĐ."""
    items,status = read_yccd(bai,path)
    if not items:
        raise PedagogyValidationError(f'Không có YCCĐ bài {bai}: {status}')
    return items
