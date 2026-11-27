"""Công thức metric dùng chung cho Benchmark, Human Eval và kiểm toán số liệu.

Mọi công thức viết tay (không phụ thuộc sklearn/scipy) để cả nhóm đọc và giải thích được khi vấn đáp.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence

# ----------------------------------------------------------------------------
# Retrieval
# ----------------------------------------------------------------------------


def recall_at_k(ranked_relevance: Sequence[object], gold: Iterable[object], k: int = 5) -> Optional[float]:
    """Recall@k = |gold ∩ top-k| / |gold|.  `ranked_relevance`: danh sách id (vd số trang) theo thứ hạng."""
    gold_set = set(gold)
    if not gold_set:
        return None
    hit = gold_set.intersection(ranked_relevance[:k])
    return len(hit) / len(gold_set)


def reciprocal_rank(ranked: Sequence[object], gold: Iterable[object]) -> Optional[float]:
    """RR = 1 / thứ hạng của kết quả đúng đầu tiên (0 nếu không có). MRR = trung bình RR."""
    gold_set = set(gold)
    if not gold_set:
        return None
    for i, item in enumerate(ranked, start=1):
        if item in gold_set:
            return 1.0 / i
    return 0.0


# ----------------------------------------------------------------------------
# Thống kê mô tả
# ----------------------------------------------------------------------------


def mean(values: Iterable[Optional[float]]) -> Optional[float]:
    vals = [float(v) for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def percentile(values: Sequence[float], q: float) -> Optional[float]:
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return None
    pos = (len(vals) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def safe_div(a: float, b: float) -> Optional[float]:
    return a / b if b else None


# ----------------------------------------------------------------------------
# Độ đồng thuận (Human Eval vs LLM-as-a-Judge)
# ----------------------------------------------------------------------------


def cohen_kappa(a: Sequence[int], b: Sequence[int], weights: Optional[str] = "quadratic", labels: Optional[Sequence[int]] = None) -> Optional[float]:
    """Cohen's κ. weights=None (không trọng số) | 'linear' | 'quadratic' (khuyên dùng cho thang 1–5).

    κ = 1 − Σ w_ij·O_ij / Σ w_ij·E_ij ;  w_ij = 0 trên đường chéo.
    """
    if len(a) != len(b) or not a:
        return None
    labels = sorted(set(labels or []) | set(a) | set(b))
    idx = {v: i for i, v in enumerate(labels)}
    k, n = len(labels), len(a)
    if k == 1:
        return 1.0
    obs = [[0.0] * k for _ in range(k)]
    for x, y in zip(a, b):
        obs[idx[x]][idx[y]] += 1
    ra = [sum(row) for row in obs]
    rb = [sum(obs[i][j] for i in range(k)) for j in range(k)]

    def w(i, j):
        if weights is None:
            return 0.0 if i == j else 1.0
        d = abs(i - j) / (k - 1)
        return d if weights == "linear" else d * d

    num = sum(w(i, j) * obs[i][j] for i in range(k) for j in range(k))
    den = sum(w(i, j) * ra[i] * rb[j] / n for i in range(k) for j in range(k))
    if den == 0:
        return 1.0 if num == 0 else 0.0
    return 1 - num / den


def _rank(values: Sequence[float]) -> List[float]:
    """Hạng trung bình cho giá trị bằng nhau (ties)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for t in range(i, j + 1):
            ranks[order[t]] = avg
        i = j + 1
    return ranks


def pearson(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    n = len(x)
    if n != len(y) or n < 2:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def spearman_rho(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    """Spearman ρ = Pearson trên hạng (xử lý ties bằng hạng trung bình)."""
    if len(x) != len(y) or len(x) < 2:
        return None
    return pearson(_rank(list(x)), _rank(list(y)))


def interpret_kappa(k: Optional[float]) -> str:
    """Thang Landis & Koch (1977)."""
    if k is None:
        return "không xác định"
    if k < 0:
        return "kém (poor)"
    for cut, label in ((0.2, "rất thấp (slight)"), (0.4, "thấp (fair)"), (0.6, "trung bình (moderate)"), (0.8, "khá (substantial)")):
        if k <= cut:
            return label
    return "gần như tuyệt đối (almost perfect)"


def to_likert(score01: Optional[float]) -> Optional[int]:
    """0–1 → 1–5 (ngược với chuẩn hoá (s-1)/4 của Critic)."""
    if score01 is None:
        return None
    return int(round(1 + 4 * max(0.0, min(1.0, score01))))


def counter_top(items: Iterable[str], n: int = 10) -> List[tuple]:
    return Counter(items).most_common(n)
