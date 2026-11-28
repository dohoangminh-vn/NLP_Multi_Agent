from types import SimpleNamespace as NS
import json
import pytest
from src.utils.tv3_bridge import to_citation,normalize_chunks,structured_feedback,CriticBridge
from src.utils.pedagogy_validator import PedagogyValidationError

C=dict(doc_name='Fixture',bai_so=20,page_number=105,section_title='for',quoted_text='fixture text')

@pytest.mark.parametrize('c',[C,NS(**C),{'metadata':{'doc_name':'Fixture','bai_so':20,'page_number':105,'muc':'for'},'content':'fixture text'}, {'doc_name':'Fixture','bai_so':20,'page_number':105,'muc':'for','text':'fixture text'}])
def test_citation(c): assert to_citation(c)==C

def test_filter(): assert normalize_chunks([C,C,dict(C,bai_so=21),dict(C,bai_so=19)],20)==[C]

@pytest.mark.parametrize('changes',[{'page_number':0},{'section_title':''},{'quoted_text':None},{'bai_so':True}])
def test_invalid_citation(changes):
    with pytest.raises(PedagogyValidationError): to_citation(dict(C,**changes))


def result(gates=('AST: 1 vi phạm', 'Sandbox: 1 lỗi')):
    return NS(report=NS(is_passed=False,ast_violations=['AST lỗi'],sandbox_failures=['Sandbox lỗi']),gate_failures=list(gates),ast_results=[NS(passed=True),NS(passed=False),NS(passed=True)],sandbox_results=[NS(passed=True),NS(passed=True),NS(passed=False)])


def test_feedback():
    raw=structured_feedback(result())
    assert json.loads(raw)['targets']==['worksheets.1.starter_code','worksheets.1.solution_code','worksheets.2.solution_code']

@pytest.mark.parametrize('gate',['YCCĐ Coverage 0','Thời lượng 40/45','Faithfulness 0','Citation Precision 0'])
def test_no_guess(gate): assert structured_feedback(result(['AST: 1',gate])) is None

def test_missing_history():
    class C:
        history=[]
        def __call__(self,s): return {'critic_report':{'is_passed':False,'revision_instructions':'text'}}
    assert CriticBridge(C())({})['critic_report']['revision_instructions']=='text'

def test_bridge_preserves_metrics():
    class C:
        def __init__(self): self.history=[]
        def __call__(self,s):
            self.history.append(result())
            return {'critic_report':{'is_passed':False,'faithfulness_score':.9,'revision_instructions':'text'},'iteration':1}
    c=C(); out=CriticBridge(c)({}); assert out['iteration']==1 and out['critic_report']['faithfulness_score']==.9
    assert len(json.loads(out['critic_report']['revision_instructions'])['targets'])==3
