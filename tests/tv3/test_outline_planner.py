import json
from types import SimpleNamespace
import pytest
from src.agents.outline_planner import OutlinePlanner, dump
from src.agents.profiler import build_profile
from src.utils.pedagogy_validator import PedagogyValidationError, validate_outline

REQ = dict(target_bai=20, topic_name='Vòng lặp for', scope_mode='45m_theory', target_minutes=45, basic_ratio=.3, standard_ratio=.5, advanced_ratio=.2, lab_constraints='')
YCCD = [{'code':'F10.B20.YC1','text':'Sử dụng vòng lặp for'}]
def planner(llm=None, items=YCCD):
    return OutlinePlanner(llm, lambda bai: items)
def profile(req=REQ):
    return build_profile(req, taxonomy_path=None)
class Fake:
    def __init__(self, result): self.result = result
    def complete(self, system, user):
        assert 'YCCĐ' in system
        assert json.loads(user)['yccd'] == YCCD
        if isinstance(self.result, Exception): raise self.result
        return SimpleNamespace(text=self.result)

@pytest.mark.parametrize('mode,minutes',[('45m_theory',45),('45m_practice',45),('90m_full',90)])
def test_offline(mode,minutes):
    req={**REQ,'scope_mode':mode,'target_minutes':minutes}
    result=dump(planner().plan(req,profile(req)))
    validate_outline(result,req)
    assert not result['is_approved']
    assert 'F10.B20.YC1' in result['objective_summary']
    assert sum(result['activity_minutes'])==minutes

def test_missing():
    with pytest.raises(PedagogyValidationError): planner(items=[]).plan(REQ,profile())
def test_wrong_lesson():
    with pytest.raises(PedagogyValidationError): planner(items=[{'code':'F10.B21.YC1','text':'while'}]).plan(REQ,profile())
def test_wrong_profile():
    with pytest.raises(PedagogyValidationError): planner().plan(REQ,{**profile(),'target_bai':21})
def test_node():
    out=planner()({'request':REQ,'bloom_matrix_json':json.dumps(profile())})
    assert not dump(out['outline'])['is_approved']
@pytest.mark.parametrize('response',['bad','[]','{}',RuntimeError('timeout'),json.dumps({'activity_titles':['A']*4,'worksheet_goals':['x']*3})])
def test_fallback(response,caplog):
    assert dump(planner(Fake(response)).plan(REQ,profile()))==dump(planner().plan(REQ,profile()))
    assert 'fallback' in caplog.text

def test_llm_control():
    base=dump(planner().plan(REQ,profile()))
    candidate={**base,'activity_titles':[x.split(':')[0]+': nhiệm vụ mới' for x in base['activity_titles']], 'activity_minutes':[-1,1,1,44], 'objective_summary':'bịa', 'is_approved':True}
    out=dump(planner(Fake(json.dumps(candidate))).plan(REQ,profile()))
    assert out['activity_titles']==candidate['activity_titles']
    assert out['activity_minutes']==base['activity_minutes']
    assert out['objective_summary']==base['objective_summary']
    assert not out['is_approved']

def test_bad_tiers():
    base=dump(planner().plan(REQ,profile()))
    candidate={**base,'worksheet_goals':list(reversed(base['worksheet_goals']))}
    assert dump(planner(Fake(json.dumps(candidate))).plan(REQ,profile()))==base
