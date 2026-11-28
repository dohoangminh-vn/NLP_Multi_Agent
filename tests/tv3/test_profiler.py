import json
from types import SimpleNamespace
import pytest
from src.agents.profiler import ACTIVITIES, ClassProfiler, allocate_minutes, build_profile
from src.utils.pedagogy_validator import PedagogyValidationError


def req(**changes):
    return dict(target_bai=20, scope_mode='45m_theory', target_minutes=45,
                basic_ratio=.25, standard_ratio=.5, advanced_ratio=.25,
                lab_constraints='', **{}) | changes


@pytest.mark.parametrize('mode,total', [('45m_theory',45),('45m_practice',45),('90m_full',90)])
@pytest.mark.parametrize('ratios', [(1,0,0),(0,1,0),(0,0,1),(.25,.5,.25),(.45,.2,.35),(.1,.2,.7)])
def test_minutes(mode,total,ratios):
    r=req(scope_mode=mode,target_minutes=total,basic_ratio=ratios[0],standard_ratio=ratios[1],advanced_ratio=ratios[2])
    m=allocate_minutes(r)
    assert len(m)==4 and sum(m)==total and all(type(x) is int and x>0 for x in m)
    assert m==allocate_minutes(r)


@pytest.mark.parametrize('changes',[dict(target_bai=30),dict(target_minutes=1),dict(scope_mode='90m_full'),dict(basic_ratio=0,standard_ratio=0,advanced_ratio=0),dict(basic_ratio=-.1),dict(advanced_ratio=float('nan'))])
def test_invalid(changes):
    with pytest.raises(PedagogyValidationError): build_profile(req(**changes),None)


def test_node_contract():
    r=req()
    p=json.loads(ClassProfiler(None)({'request':r})['bloom_matrix_json'])
    assert p==build_profile(SimpleNamespace(**r),None)
    assert [a['activity'] for a in p['activities']]==ACTIVITIES
    assert len(p['tiers'])==3 and p['curriculum_status']=='missing_taxonomy'
    assert all(t['competencies']==[] for t in p['tiers'])


def test_lab():
    p=build_profile(req(lab_constraints='2 HS/1 máy; không có máy chiếu; mất mạng'),None)
    assert len(p['lab_notes'])==3


def test_no_lab():
    assert 'thực hành bù' in build_profile(req(lab_constraints='Không có phòng máy'),None)['lab_notes'][0]


def test_taxonomy(tmp_path):
    f=tmp_path/'taxonomy.json'
    f.write_text(json.dumps({'lessons':[{'bai_so':20,'yccd_items':[{'yccd_id':'F10.B20.YC1','description':'Ví dụ kiểm thử','competency_codes':['NLc'],'bloom_levels':['Hiểu']}]}]}),encoding='utf-8')
    p=build_profile(req(),f)
    assert p['curriculum_status']=='mapped_from_tv2'
    assert all(t['competencies']==['NLc'] for t in p['tiers'])
    assert build_profile(req(target_bai=19),f)['curriculum_status']=='lesson_not_unique_or_missing'


def test_legacy_taxonomy(tmp_path):
    f=tmp_path/'taxonomy.json'; f.write_text('{}',encoding='utf-8')
    assert build_profile(req(),f)['curriculum_status']=='unsupported_taxonomy'


def test_zero_ratio_keeps_three_tiers():
    p=build_profile(req(basic_ratio=0,standard_ratio=1,advanced_ratio=0),None)
    assert [t['active_in_class'] for t in p['tiers']]==[False,True,False]
