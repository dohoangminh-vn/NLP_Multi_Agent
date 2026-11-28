"""Apply only explicitly authorized field replacements; no free-text target guessing."""
import copy
import json
import re
from src.utils.pedagogy_validator import PedagogyValidationError


def check(ok, message):
    if not ok:
        raise PedagogyValidationError('Revision: ' + message)


ACT = {'goal','content','expected_product','step1_transfer','step2_execute','step3_report','step4_conclude','citations'}
WORK = {'prompt_text','starter_code','solution_code','test_cases'}
ROOT = {'general_competencies','cs_competencies','qualities','teaching_materials'}


def path_parts(path):
    check(isinstance(path, str), 'target must be a string')
    if path in ROOT:
        return [path]
    m = re.fullmatch(r'(activities|worksheets)\.([0-9])\.([a-z0-9_]+)', path)
    check(m is not None, 'invalid or protected target')
    group, index, field = m.groups()
    index = int(index)
    check((group == 'activities' and index < 4 and field in ACT) or
          (group == 'worksheets' and index < 3 and field in WORK), 'invalid or protected target')
    return [group,index,field]


def parse_instruction(raw):
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        raise PedagogyValidationError('Revision: Critic must provide structured targets; free text is not authorization') from None
    check(isinstance(obj,dict) and set(obj)=={'targets','instructions'}, 'invalid instruction format')
    targets = obj['targets']
    check(isinstance(targets,list) and 0 < len(targets) <= 12, 'need 1–12 targets')
    for p in targets:
        path_parts(p)
    check(len(set(targets))==len(targets), 'duplicate targets')
    check(isinstance(obj['instructions'],str) and bool(obj['instructions'].strip()), 'missing instructions')
    return obj


def apply_patch(previous, targets, patch):
    check(isinstance(patch,dict) and set(patch)=={'replacements'}, 'expected replacements object')
    replacements=patch['replacements']
    check(isinstance(replacements,dict) and set(replacements)==set(targets), 'replacement paths differ from authorized targets')
    result=copy.deepcopy(previous)
    changed=False
    for path in targets:
        parts=path_parts(path)
        parent=result
        for part in parts[:-1]:
            parent=parent[part]
        check(parts[-1] in parent, 'target does not exist')
        value=replacements[path]
        changed |= parent[parts[-1]] != value
        parent[parts[-1]]=copy.deepcopy(value)
    check(changed, 'no change')
    return result
