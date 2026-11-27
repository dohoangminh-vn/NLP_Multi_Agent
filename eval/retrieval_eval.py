"""Đánh giá tầng truy hồi (TV2): Recall@5, Hit@5, MRR trên eval/retrieval_queries.json.

So sánh: BM25 thuần (không tách từ) · BM25 + VN segmentation · Dense · Hybrid · Hybrid + Reranker.

    python -m eval.retrieval_eval            # → eval/results/retrieval_metrics.json
    python -m eval.retrieval_eval --neural   # dùng sentence-transformers + cross-encoder (nếu đã cài)

Định nghĩa (đơn vị = (bài, trang in)):
* Recall@5 = |trang đúng ∩ trang của top-5 chunk| / |trang đúng|
* Hit@5    = 1 nếu top-5 có ít nhất 1 trang đúng
* MRR      = trung bình 1/hạng của chunk đúng đầu tiên
* Leak     = số chunk có bai_so > target_bai trong top-5 (PHẢI = 0)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.metrics import mean, recall_at_k, reciprocal_rank  # noqa: E402
from src.rag.hybrid_search import HybridSearcher, PlainTokenizer, VnTokenizer  # noqa: E402
from src.rag.ingest import load_chunks  # noqa: E402

QUERIES = ROOT / "eval" / "retrieval_queries.json"
OUT = ROOT / "eval" / "results" / "retrieval_metrics.json"


def evaluate(searcher: HybridSearcher, queries, mode: str, rerank: bool, k: int = 5) -> dict:
    rec, hit, rr, leaks, per = [], [], [], 0, []
    t0 = time.perf_counter()
    for q in queries:
        hits = searcher.search(q["query"], q["target_bai"], k=k, mode=mode, rerank=rerank)
        ranked = [(h.chunk.bai_so, h.chunk.page_number) for h in hits]
        gold = {(q["gold_bai"], p) for p in q["gold_pages"]}
        r = recall_at_k(ranked, gold, k)
        rec.append(r)
        hit.append(1.0 if gold.intersection(ranked) else 0.0)
        rr.append(reciprocal_rank(ranked, gold))
        leaks += sum(1 for b, _ in ranked if b > q["target_bai"])
        per.append({"qid": q["qid"], "recall_at_5": r, "rr": rr[-1], "top": [f"B{b}-tr{p}" for b, p in ranked]})
    return {"recall_at_5": round(mean(rec), 4), "hit_at_5": round(mean(hit), 4), "mrr": round(mean(rr), 4),
            "leaks": leaks, "ms_per_query": round(1000 * (time.perf_counter() - t0) / len(queries), 2), "per_query": per}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--neural", action="store_true")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    queries = json.loads(QUERIES.read_text(encoding="utf-8"))["queries"]
    chunks = load_chunks()
    plain = HybridSearcher(chunks, tokenizer=PlainTokenizer())
    vn = HybridSearcher.build_default(neural=args.neural) if args.neural else HybridSearcher(chunks, tokenizer=VnTokenizer())
    configs = {
        "bm25_plain": (plain, "bm25", False),
        "bm25_vnseg": (vn, "bm25", False),
        "dense": (vn, "dense", False),
        "hybrid": (vn, "hybrid", False),
        "hybrid_rerank": (vn, "hybrid", True),
    }
    results = {name: evaluate(s, queries, mode, rr) for name, (s, mode, rr) in configs.items()}
    meta = {"n_queries": len(queries), "n_chunks": len(chunks), "tokenizer": vn.tok.name,
            "embedder": vn.embedder.name, "reranker": getattr(vn.reranker, "name", "none")}
    summary = {k: {m: v[m] for m in ("recall_at_5", "hit_at_5", "mrr", "leaks", "ms_per_query")} for k, v in results.items()}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"meta": meta, "summary": summary, "details": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))
    print(f"{'Cấu hình':<15} {'Recall@5':>9} {'Hit@5':>7} {'MRR':>7} {'Leak':>5} {'ms/q':>7}")
    for k, v in summary.items():
        print(f"{k:<15} {v['recall_at_5']:>9.3f} {v['hit_at_5']:>7.3f} {v['mrr']:>7.3f} {v['leaks']:>5} {v['ms_per_query']:>7.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
