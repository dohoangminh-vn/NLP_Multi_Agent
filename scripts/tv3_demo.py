"""Generate offline demonstration documents from actual local corpus or supplied RAG chunks.
Real LLM is opt-in; generated code is not executed by this CLI.
Run from the isolated combined tree: python -m scripts.tv3_demo --help
"""
import argparse
import json
from functools import partial
from pathlib import Path
from src.utils.tv3_integration import run_pipeline, export_verified
from src.utils.tv3_bridge import CriticBridge, normalize_chunks
from src.utils.yccd_adapter import load_yccd
from src.utils.prompt_builder import dump


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--taxonomy',type=Path,default=Path('data/yccd_taxonomy.json'))
    p.add_argument('--chunks',type=Path,default=Path('data/processed/sgk_chunks.jsonl'),help='JSONL of actual source chunks, not a generated fixture')
    p.add_argument('--live-retriever',action='store_true',help='Use Khánh snapshot CurriculumRetriever; requires its corpus/index')
    p.add_argument('--lessons',type=int,nargs='+',default=[16,19,20,21,25,29])
    p.add_argument('--mode',choices=['45m_theory','45m_practice','90m_full'],default='90m_full')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--demo-bypass-hitl',action='store_true',help='Demo only: explicitly bypass human approval')
    p.add_argument('--llm',action='store_true',help='Use team LLM provider from environment; no generated-code execution or automatic Critic approval')
    a=p.parse_args()
    from src.schemas import LessonRequest
    from src.tools.ast_guard import scope_summary
    loader=partial(load_yccd,path=a.taxonomy)
    taxonomy=json.loads(a.taxonomy.read_text(encoding='utf-8-sig'))
    titles={x['bai_so']:x.get('ten_bai',f"Bài {x['bai_so']}") for x in taxonomy.get('lessons',[])}
    if a.live_retriever:
        from src.agents.retriever import CurriculumRetriever
        retriever=CurriculumRetriever(yccd_loader=loader)
    else:
        corpus=[json.loads(line) for line in a.chunks.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    llm=None
    if a.llm:
        from src.utils.llm import llm_from_env
        llm=llm_from_env(temperature=0)
    def approve(outline):
        d=dump(outline)
        print(json.dumps(d,ensure_ascii=False,indent=2))
        d['is_approved']=input('Duyệt khung này? (yes/no): ').strip().lower()=='yes'
        return d
    a.output.mkdir(parents=True,exist_ok=True)
    reports=[]
    for bai in a.lessons:
        req=LessonRequest(target_bai=bai,topic_name=titles.get(bai,f'Bài {bai}'),scope_mode=a.mode,target_minutes=90 if a.mode=='90m_full' else 45,bypass_hitl=a.demo_bypass_hitl)
        try:
            if a.live_retriever:
                chunks=retriever.retrieve(req)
                meta=retriever.last_meta
            else:
                candidates=[x for x in corpus if (x.get('metadata') or x).get('bai_so')==bai]
                chunks=normalize_chunks(candidates,bai)[:8]
                meta={'source':'local JSONL, first 8 same-lesson chunks; not a retrieval benchmark'}
            critic=None
            if not a.llm:
                # Only the inherited, trusted worksheet bank is executed here.
                from src.agents.critic import CriticAgent,CriticConfig
                critic=CriticBridge(CriticAgent(config=CriticConfig(use_llm=False,strict_ast=True),yccd_loader=loader))
            result=run_pipeline(req,chunks,a.taxonomy,llm=llm,critic=critic,scope_loader=scope_summary,approve=approve)
            record={'lesson':bai,'status':result['status'],'request':dump(req),'retrieval':meta,'human_approval_bypassed':a.demo_bypass_hitl,'llm':a.llm}
            if result.get('critic_report') is not None:
                record['critic_report']=dump(result['critic_report'])
            if result['status'] in ('offline_checks_passed','unverified_draft','critic_passed'):
                paths=export_verified(result,a.output/f'B{bai}',allow_draft=True)
                record['files']=[str(x) for x in paths]
            reports.append(record)
        except Exception as exc:
            # Do not include provider exceptions (may contain credentials) in shared reports.
            reports.append({'lesson':bai,'status':'error','error_type':type(exc).__name__})
            print(f'Bài {bai}: lỗi {type(exc).__name__}; kiểm tra dữ liệu và cấu hình.')
        print(f'Bài {bai}: {reports[-1]["status"]}')
        (a.output/'report.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf8')
    if any(x['status']=='error' for x in reports):
        raise SystemExit(1)

if __name__=='__main__': main()
