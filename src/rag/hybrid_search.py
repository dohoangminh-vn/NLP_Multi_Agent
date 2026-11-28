"""Vietnamese Hybrid Search (TV2): VN-BM25 + Dense + lọc `bai_so <= k` + RRF + Cross-Encoder Reranker.

* Nạp index do `src/rag/ingest.py` dựng (token đã tách từ tiếng Việt bằng pyvi + vector); nếu index
  không khớp tập chunk hiện tại thì tự dựng lại trong bộ nhớ.
* BM25 Okapi viết tay (k1=1.5, b=0.75).
* Dense: `SentenceTransformerEmbedder` (vd `bkai-foundation-models/vietnamese-bi-encoder`,
  `intfloat/multilingual-e5-small`) nếu cài sentence-transformers; nếu không dùng
  `CharNgramEmbedder` (TF-IDF n-gram kí tự, KHÔNG phải mô hình nơ-ron – ghi rõ trong báo cáo).
* Lọc metadata CỨNG trước khi chấm điểm: chỉ chunk có bai_so <= target_bai.
* Hợp nhất Reciprocal Rank Fusion (k=60), rerank top-N bằng Cross-Encoder (nếu có) hoặc LexicalReranker.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np

from src.rag.ingest import (  # tách từ VN + vector được dựng ở bước ingest
    CharNgramEmbedder,
    Chunk,
    Embedder,
    SentenceTransformerEmbedder,
    VnTokenizer,
    index_text,
    load_chunks,
    load_index,
)


class BM25:
    def __init__(self, docs: Sequence[List[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avg = sum(self.len) / max(1, len(docs))
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: List[str], allowed: Sequence[int]) -> Dict[int, float]:
        out = {}
        for i in allowed:
            tf, s = self.tf[i], 0.0
            for t in query:
                f = tf.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
            if s > 0:
                out[i] = s
        return out


class LexicalReranker:
    """Reranker dự phòng: độ phủ token truy vấn + khớp cụm 2 từ liên tiếp (không cần mô hình)."""

    name = "lexical"

    def __init__(self, tokenizer):
        self.tok = tokenizer

    def score(self, query: str, texts: Sequence[str]) -> List[float]:
        q = self.tok(query)
        qs, qb = set(q), {f"{a} {b}" for a, b in zip(q, q[1:])}
        out = []
        for t in texts:
            d = self.tok(t)
            ds, db = set(d), {f"{a} {b}" for a, b in zip(d, d[1:])}
            cov = len(qs & ds) / max(1, len(qs))
            big = len(qb & db) / max(1, len(qb)) if qb else 0.0
            out.append(cov + 0.5 * big)
        return out


class CrossEncoderReranker:  # pragma: no cover - cần tải mô hình
    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3"):
        from sentence_transformers import CrossEncoder

        self.model = CrossEncoder(model_name)
        self.name = model_name

    def score(self, query, texts):
        return [float(s) for s in self.model.predict([(query, t) for t in texts])]


@dataclass
class SearchHit:
    chunk: Chunk
    score: float
    rank: int

    def to_citation(self) -> dict:
        return self.chunk.to_citation()


class HybridSearcher:
    def __init__(self, chunks: Optional[List[Chunk]] = None, tokenizer=None, embedder: Optional[Embedder] = None,
                 reranker=None, rrf_k: int = 60):
        self.chunks = chunks if chunks is not None else load_chunks()
        self.tok = tokenizer or VnTokenizer()
        self.texts = [index_text(c) for c in self.chunks]
        cached = load_index(self.chunks, self.tok.name) if embedder is None else None
        tokens, idx_emb = cached if cached else ([self.tok(t) for t in self.texts], None)
        self.index_loaded = cached is not None
        self.bm25 = BM25(tokens)
        self.embedder = embedder or idx_emb or CharNgramEmbedder().fit(self.texts)
        self.emb = self.embedder.encode(self.texts)
        self.reranker = reranker if reranker is not None else LexicalReranker(self.tok)
        self.rrf_k = rrf_k

    @classmethod
    def build_default(cls, neural: bool = False) -> "HybridSearcher":
        if neural:
            return cls(embedder=SentenceTransformerEmbedder(), reranker=CrossEncoderReranker())
        return cls()

    def _allowed(self, target_bai: int, doc_types: Optional[Sequence[str]]) -> List[int]:
        return [i for i, c in enumerate(self.chunks) if c.bai_so <= target_bai and (not doc_types or c.doc_type in doc_types)]

    def search(self, query: str, target_bai: int, k: int = 5, mode: str = "hybrid", rerank: bool = True,
               doc_types: Optional[Sequence[str]] = None, candidates: int = 20) -> List[SearchHit]:
        allowed = self._allowed(target_bai, doc_types)  # LỌC CỨNG bai_so <= k
        if not allowed:
            return []
        ranked: Dict[str, List[int]] = {}
        if mode in ("bm25", "hybrid"):
            s = self.bm25.scores(self.tok(query), allowed)
            ranked["bm25"] = sorted(s, key=s.get, reverse=True)[:candidates * 2]
        if mode in ("dense", "hybrid"):
            q = self.embedder.encode([query], is_query=True)[0]
            sims = self.emb[allowed] @ q
            order = np.argsort(-sims)[: candidates * 2]
            ranked["dense"] = [allowed[j] for j in order]
        fused: Dict[int, float] = {}
        for lst in ranked.values():
            for r, i in enumerate(lst):
                fused[i] = fused.get(i, 0.0) + 1.0 / (self.rrf_k + r + 1)
        top = sorted(fused, key=fused.get, reverse=True)[:candidates]
        if rerank and self.reranker is not None and top:
            rs = self.reranker.score(query, [self.texts[i] for i in top])
            # kết hợp: điểm reranker là chính, RRF phá hoà
            order = sorted(range(len(top)), key=lambda j: (rs[j], fused[top[j]]), reverse=True)
            top = [top[j] for j in order]
            scores = [rs[j] for j in order]
        else:
            scores = [fused[i] for i in top]
        return [SearchHit(self.chunks[i], float(s), r + 1) for r, (i, s) in enumerate(zip(top[:k], scores[:k]))]
