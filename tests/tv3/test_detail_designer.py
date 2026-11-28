import copy
import json
from types import SimpleNamespace
import pytest
from src.agents.detail_designer import DetailDesigner
from src.utils.pedagogy_validator import PedagogyValidationError
from src.utils.prompt_builder import dump

REQ=dict(target_bai=20,scope_mode='45m_theory',target_minutes=45,basic_ratio=.3,standard_ratio=.5,advanced_ratio=.2)
OUT=dict(objective_summary='Mục tiêu mẫu',activity_titles=['Khởi động','Hình thành kiến thức','Luyện tập','Vận dụng'],activity_minutes=[5,20,12,8],worksheet_goals=['Cơ bản','Đạt chuẩn','Nâng cao'],is_approved=True)
PROFILE={k:REQ[k] for k in ('target_bai','scope_mode','target_minutes')}
CHUNK=dict(doc_name='SGK',bai_so=20,section_title='for',page_number=105,quoted_text='Ví dụ vòng lặp')
YCCD=[dict(code='F10.B20.YC1',text='Mục tiêu mẫu',competency_codes=['NLc'])]

class Fake:
    def __init__(self,*responses): self.responses=list(responses); self.calls=0
    def complete(self,system,user):
        result=self.responses[min(self.calls,len(self.responses)-1)]; self.calls+=1
        return SimpleNamespace(text=result)

def design(llm=None,**changes):
    args=dict(req=REQ,outline=OUT,profile=PROFILE,chunks=[CHUNK]);args.update(changes)
    return dump(DetailDesigner(llm,yccd_loader=lambda _:YCCD).design(**args))

def test_offline():
    result=design(); assert len(result['activities'])==4 and len(result['worksheets'])==3
    assert result['knowledge_goals']==OUT['objective_summary']

def test_valid_llm():
    p=design(); f=Fake(json.dumps(p)); assert design(f)==p and f.calls==1

def test_retry():
    p=design(); f=Fake('not json',json.dumps(p)); assert design(f)==p and f.calls==2

def test_fenced():
    p=design(); assert design(Fake('```json\n'+json.dumps(p)+'\n```'))==p

@pytest.mark.parametrize('mutation',[
    lambda p:p.update(knowledge_goals='khác'),
    lambda p:p['activities'][0].update(duration_minutes=-5),
    lambda p:p['activities'][0].update(activity_name='Sai'),
    lambda p:p['activities'][0].update(step1_transfer=' '),
    lambda p:p['activities'][0].update(citations=[]),
    lambda p:p['activities'][0]['citations'][0].update(page_number=99),
    lambda p:p['activities'].pop(),
    lambda p:p['worksheets'][0].update(tier='Nâng cao'),
    lambda p:p['worksheets'][0].update(bloom_level='Vận dụng'),
    lambda p:p['worksheets'][0].update(test_cases=[]),
    lambda p:p['worksheets'][0].update(starter_code=p['worksheets'][0]['solution_code']),
    lambda p:p['worksheets'][0]['test_cases'][0].update(is_edge_case='false'),
])
def test_invalid(mutation):
    p=design(); mutation(p); f=Fake(json.dumps(p))
    with pytest.raises(PedagogyValidationError): design(f)
    assert f.calls==2

@pytest.mark.parametrize('changes',[{'outline':dict(OUT,is_approved=False)},{'chunks':[]},{'chunks':[dict(CHUNK,bai_so=21)]},{'profile':dict(PROFILE,target_bai=21)}])
def test_preconditions(changes):
    f=Fake('{}')
    with pytest.raises(PedagogyValidationError): design(f,**changes)
    assert f.calls==0

def test_revision_not_silent():
    with pytest.raises(PedagogyValidationError,match='Revision'): design(previous=design(),revision='Sửa phiếu')

def test_node():
    result=DetailDesigner(yccd_loader=lambda _:YCCD)({'request':REQ,'outline':OUT,'bloom_matrix_json':json.dumps(PROFILE),'retrieved_chunks':[CHUNK]})
    assert dump(result['draft_plan'])['activities'][0]['duration_minutes']==5
