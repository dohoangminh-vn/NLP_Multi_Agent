"""Run with the isolated team overlay; skip missing external contracts explicitly."""
import copy
from functools import partial
from pathlib import Path
import pytest
from src.agents.profiler import ClassProfiler
from src.agents.outline_planner import OutlinePlanner
from src.agents.detail_designer import DetailDesigner
from src.utils.tv3_integration import run_pipeline
from src.utils.tv3_bridge import CriticBridge, to_citation
from src.utils.yccd_adapter import load_yccd
from src.utils.prompt_builder import dump
from test_tv3_integration import req,chunks

ROOT=Path(__file__).resolve().parents[2]
TAX=ROOT/'data'/'yccd_taxonomy.json'

def team():
    schema=pytest.importorskip('src.schemas')
    critic=pytest.importorskip('src.agents.critic')
    guard=pytest.importorskip('src.tools.ast_guard')
    if not TAX.exists(): pytest.skip('Team taxonomy not installed')
    return schema,critic,guard

@pytest.mark.parametrize('b',range(16,30))
def test_real_schema_guard_sandbox_critic(b):
    schema,critic,guard=team()
    c=critic.CriticAgent(config=critic.CriticConfig(use_llm=False,strict_ast=True),yccd_loader=partial(load_yccd,path=TAX))
    result=run_pipeline(schema.LessonRequest(**req(b)),chunks(b),TAX,critic=CriticBridge(c),scope_loader=guard.scope_summary)
    assert isinstance(result['draft_plan'],schema.LessonPlan5512)
    report=result['critic_report']
    assert result['status']=='offline_checks_passed', dump(report)
    assert report.total_minutes_checked==45 and report.is_passed
    assert len(c.history[-1].ast_results)==3 and len(c.history[-1].sandbox_results)==3
    assert all(x.passed for x in c.history[-1].ast_results+c.history[-1].sandbox_results)


def test_real_critic_bridge():
    schema,critic,guard=team()
    request=schema.LessonRequest(**req())
    state=run_pipeline(request,chunks(20),TAX)
    p=dump(state['draft_plan']);p['worksheets'][0]['solution_code']='print(0)'
    state['draft_plan']=schema.LessonPlan5512.model_validate(p)
    c=critic.CriticAgent(config=critic.CriticConfig(use_llm=False,strict_ast=True),yccd_loader=partial(load_yccd,path=TAX))
    update=CriticBridge(c)(state)
    import json
    assert not update['critic_report'].is_passed
    assert json.loads(update['critic_report'].revision_instructions)['targets']==['worksheets.0.solution_code']


def test_real_viet_dto():
    model=pytest.importorskip('src.rag.models')
    d=model.DocumentChunk(chunk_id='fixture',content='Dữ liệu fixture',metadata=dict(doc_type='sgk',doc_name='Fixture',bai_so=20,ten_bai='for',muc='Mục',page_number=105,loai_noi_dung='ly_thuyet'))
    assert to_citation(d)==dict(doc_name='Fixture',bai_so=20,section_title='Mục',page_number=105,quoted_text='Dữ liệu fixture')


def test_graph_fixture():
    pytest.importorskip('langgraph')
    schema,critic,guard=team()
    from src.graph.workflow import build_graph
    class Retriever:
        def retrieve(self,r): return [schema.SourceCitation(**x) for x in chunks(r.target_bai)]
    loader=partial(load_yccd,path=TAX)
    graph=build_graph(retriever=Retriever(),profiler=ClassProfiler(TAX),outline_planner=OutlinePlanner(yccd_loader=loader),designer=DetailDesigner(yccd_loader=loader,scope_loader=guard.scope_summary),critic_node=critic.CriticAgent(config=critic.CriticConfig(use_llm=False,strict_ast=True),yccd_loader=loader),checkpointer=None)
    out=graph.invoke({'request':schema.LessonRequest(**req()),'iteration':0})
    assert out['status']=='done' and out['critic_report'].is_passed


def test_real_critic_revision_round():
    schema,critic,guard=team()
    import json
    from types import SimpleNamespace
    from src.agents.detail_designer import offline_draft
    class Scripted:
        def complete(self,system,user):
            payload=json.loads(user)
            if 'baseline' in payload:
                data=payload['baseline']
            elif 'revision' in payload:
                assert payload['revision']['targets']==['worksheets.0.solution_code']
                good=payload['context']['few_shot_examples'][0]['solution_code']
                data={'replacements':{'worksheets.0.solution_code':good}}
            else:
                data=offline_draft(payload)
                data['worksheets'][0]['solution_code']='print(0)'
            return SimpleNamespace(text=json.dumps(data,ensure_ascii=False))
    c=critic.CriticAgent(config=critic.CriticConfig(use_llm=False,strict_ast=True),yccd_loader=partial(load_yccd,path=TAX))
    out=run_pipeline(schema.LessonRequest(**req()),chunks(20),TAX,llm=Scripted(),critic=CriticBridge(c),scope_loader=guard.scope_summary)
    assert out['status']=='critic_passed' and out['iteration']==2
    assert not c.history[0].report.is_passed and c.history[1].report.is_passed
