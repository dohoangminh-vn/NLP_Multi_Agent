import json
import pytest
from src.utils.prompt_builder import build_detail_prompt, ground_context, select_examples, build_revision_prompt
from src.utils.pedagogy_validator import PedagogyValidationError

REQ = dict(target_bai=20, scope_mode='45m_theory', target_minutes=45, basic_ratio=.3, standard_ratio=.5, advanced_ratio=.2)
OUT = dict(objective_summary='YCCĐ',activity_titles=['Khởi động','Hình thành kiến thức','Luyện tập','Vận dụng'],activity_minutes=[5,20,12,8],worksheet_goals=['Cơ bản','Đạt chuẩn','Nâng cao'],is_approved=True)
PROFILE = {k:REQ[k] for k in ('target_bai','scope_mode','target_minutes')}
CHUNK = dict(doc_name='SGK',bai_so=20,section_title='for',page_number=105,quoted_text='Ví dụ vòng lặp')
YCCD = [dict(code='F10.B20.YC1',text='Mục tiêu mẫu')]


def build(**changes):
    args=dict(request=REQ,outline=OUT,profile=PROFILE,chunks=[CHUNK],yccd=YCCD)
    args.update(changes)
    return build_detail_prompt(**args)


def test_payload():
    system,user=build()
    data=json.loads(user)
    assert data['approved_outline']==OUT and data['retrieved_chunks']==[CHUNK]
    assert len(data['few_shot_examples'])==3
    assert 'LessonPlan5512' in system and 'solution_code' in system


@pytest.mark.parametrize('changes',[{'outline':dict(OUT,is_approved=False)}, {'profile':dict(PROFILE,target_bai=21)}, {'chunks':[]}, {'yccd':[]}, {'yccd':[dict(code='F10.B21.YC1',text='x')]}])
def test_reject(changes):
    with pytest.raises(PedagogyValidationError): build(**changes)


def test_bypass():
    build(request=dict(REQ,bypass_hitl=True),outline=dict(OUT,is_approved=False))


def test_context():
    assert ground_context([dict(CHUNK,bai_so=21),dict(CHUNK,page_number=0),CHUNK,CHUNK],20)==[CHUNK]


def test_budget():
    assert ground_context([dict(CHUNK,quoted_text='a'*5000),CHUNK],20,300)==[CHUNK]


def test_examples():
    assert select_examples(19)==[]
    assert all(x['solution_code'] and x['test_cases'] for x in select_examples(20))


def test_shared_bank(tmp_path):
    (tmp_path/'worksheet_bank.json').write_text(json.dumps({'lessons':{'19':{'worksheets':[{'tier':x} for x in ('Cơ bản','Đạt chuẩn','Nâng cao')]}}}),encoding='utf8')
    assert len(select_examples(19,tmp_path))==3


def test_revision():
    _,user=build_revision_prompt({'activities':[]},'Sửa phiếu 1')
    assert json.loads(user)['critic_instructions']=='Sửa phiếu 1'
    with pytest.raises(PedagogyValidationError): build_revision_prompt({},' ')
