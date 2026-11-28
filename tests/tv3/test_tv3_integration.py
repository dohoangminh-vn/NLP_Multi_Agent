"""Synthetic evidence for contract tests only, not real SGK retrieval."""
import json
import pytest
from src.utils.prompt_builder import select_examples, dump
from src.utils.tv3_integration import run_pipeline, export_verified
from src.utils.pedagogy_validator import PedagogyValidationError

@pytest.fixture
def taxonomy(tmp_path):
    p=tmp_path/'taxonomy.json'
    p.write_text(json.dumps({'lessons':[{'bai_so':b,'yccd_items':[{'yccd_id':f'F10.B{b}.YC1','description':'Mục tiêu giả lập kiểm thử','competency_codes':['NLc']}]} for b in range(16,30)]}),encoding='utf8')
    return p

def req(b=20, mode='45m_theory', bypass=True):
    return dict(target_bai=b,topic_name='Bài kiểm thử',scope_mode=mode,target_minutes=90 if mode=='90m_full' else 45,basic_ratio=.3,standard_ratio=.5,advanced_ratio=.2,lab_constraints='',bypass_hitl=bypass)

def chunks(b):
    return [dict(doc_name='FIXTURE – không phải SGK',bai_so=b,section_title='Kiểm thử',page_number=1,quoted_text='Dữ liệu giả lập để kiểm tra hợp đồng.')]

@pytest.mark.parametrize('b',range(16,30))
@pytest.mark.parametrize('mode',['45m_theory','45m_practice','90m_full'])
def test_all_lessons(taxonomy,b,mode):
    r=run_pipeline(req(b,mode),chunks(b),taxonomy)
    assert r['status']=='unverified_draft'
    p=dump(r['draft_plan'])
    assert sum(a['duration_minutes'] for a in p['activities'])==req(b,mode)['target_minutes']
    assert p['worksheets']==select_examples(b)
    assert len(p['worksheets'])==3

def test_hitl(taxonomy):
    r=run_pipeline(req(bypass=False),chunks(20),taxonomy)
    assert r['status']=='awaiting_approval' and 'draft_plan' not in r
    r=run_pipeline(req(bypass=False),chunks(20),taxonomy,approve=lambda o:{**dump(o),'is_approved':True})
    assert r['status']=='unverified_draft'

def test_failed_critic_no_export(taxonomy,tmp_path):
    r=run_pipeline(req(),chunks(20),taxonomy,critic=lambda s:{'critic_report':{'is_passed':False,'revision_instructions':'sửa tự do'}})
    assert r['status']=='needs_review'
    with pytest.raises(PedagogyValidationError): export_verified(r,tmp_path,allow_draft=True)

def test_draft_export(taxonomy,tmp_path):
    r=run_pipeline(req(),chunks(20),taxonomy)
    with pytest.raises(PedagogyValidationError): export_verified(r,tmp_path)
    assert all(p.exists() and p.name.startswith('draft_') for p in export_verified(r,tmp_path,allow_draft=True))

def test_no_source(taxonomy):
    with pytest.raises(PedagogyValidationError): run_pipeline(req(),[],taxonomy)


def test_legacy_bank_api():
    from src.agents.detail_designer import bank_worksheets
    for b in range(16,30):
        assert [dump(x) for x in bank_worksheets(b)]==select_examples(b)
