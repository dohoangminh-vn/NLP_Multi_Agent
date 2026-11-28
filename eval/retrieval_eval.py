"""Đánh giá tầng truy hồi (TV2): Recall@5, Hit@5, MRR trên eval/retrieval_queries.json.

So sánh: BM25 thuần (không tách từ) · BM25 + VN segmentation · Dense · Hybrid · Hybrid + Reranker.

    python -m eval.retrieval_eval            # → eval/results/retrieval_metrics.json
    python -m eval.retrieval_eval --neural   # dùng sentence-transformers + cross-encoder (nếu đã cài)
    python -m eval.retrieval_eval --build-queries   # sinh lại eval/retrieval_queries.json (nhãn trang từ cụm từ SGK)

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
import unicodedata
from pathlib import Path
from typing import Iterable, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rag.hybrid_search import HybridSearcher  # noqa: E402
from src.rag.ingest import FIXED_PDF, PlainTokenizer, VnTokenizer, clean_line, ensure_fixed_pdf, load_chunks  # noqa: E402
from src.tools.ast_guard import SyllabusRules  # noqa: E402

QUERIES = ROOT / "eval" / "retrieval_queries.json"
OUT = ROOT / "eval" / "results" / "retrieval_metrics.json"


# ---------------------------------------------------------------- metric


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


def mean(values) -> Optional[float]:
    vals = [float(v) for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


# ---------------------------------------------------------------- bộ truy vấn chuẩn
# Nhãn trang KHÔNG gán tay: mỗi câu hỏi có `gold_phrases` (cụm từ nguyên văn SGK);
# trang đúng = các trang (trong dải trang của bài gold) chứa cụm từ đó trong PDF đã sửa font.

# (target_bai, câu hỏi, bài chứa đáp án, [cụm từ SGK])
QUERY_SPECS = [
    (16, "Ngôn ngữ lập trình bậc cao là gì?", 16, ["Ngôn ngữ lập trình bậc cao có các câu lệnh"]),
    (16, "Chế độ gõ lệnh trực tiếp và chế độ soạn thảo chương trình Python khác nhau thế nào?", 16, ["chế độ gõ lệnh trực tiếp"]),
    (16, "Lệnh print() trong Python có chức năng gì?", 16, ["lệnh print() có chức năng"]),
    (17, "Biến là gì trong lập trình Python?", 17, ["Biến là tên (định danh) của một vùng nhớ"]),
    (17, "Thứ tự ưu tiên các phép toán luỹ thừa, nhân chia, cộng trừ", 17, ["phép luỹ thừa ** có ưu tiên cao nhất"]),
    (17, "Từ khoá của Python là gì, có đặt tên biến trùng từ khoá được không?", 17, ["luôn có một tập hợp các từ tiếng Anh"]),
    (18, "Lệnh input() dùng để nhập dữ liệu như thế nào?", 18, ["có chức năng nhập dữ liệu"]),
    (18, "Cách chuyển xâu kí tự thành số nguyên bằng lệnh int()", 18, ["chuyển đổi số thực hoặc xâu chứa số nguyên"]),
    (18, "Lệnh type() nhận biết kiểu dữ liệu của biến", 18, ["dùng để nhận biết kiểu dữ liệu"]),
    (19, "Biểu thức lôgic và các phép toán and, or, not", 19, ["Biểu thức lôgic"]),
    (19, "Cú pháp câu lệnh if dạng đủ có else", 19, ["Câu lệnh điều kiện dạng đủ"]),
    (19, "Bài toán tính tiền điện sinh hoạt theo bậc dùng lệnh if", 19, ["giá điện sinh hoạt"]),
    (20, "Lệnh range(n) trả lại vùng giá trị nào?", 20, ["Lệnh range(n) trả lại vùng giá trị"]),
    (20, "Cú pháp lệnh lặp với số lần biết trước for trong Python", 20, ["Cú pháp của lệnh lặp với số lần biết"]),
    (20, "Chương trình in các ước số của n trên một hàng ngang", 20, ["dãy các ước số của"]),
    (21, "Lệnh lặp while với số lần không biết trước", 21, ["while"]),
    (21, "Ba cấu trúc lập trình cơ bản: tuần tự, rẽ nhánh, lặp", 21, ["cấu trúc lập trình cơ bản"]),
    (21, "In các số tự nhiên từ 1 đến 100 trên một hàng ngang bằng while", 21, ["từ 1 đến 100 trên một hàng ngang"]),
    (22, "Cách khởi tạo danh sách và truy cập phần tử qua chỉ số", 22, ["Chỉ số của danh sách bắt đầu từ 0"]),
    (22, "Duyệt các phần tử của danh sách bằng for và range(len(A))", 22, ["for i in range(len(A))"]),
    (22, "Lệnh append thêm phần tử vào cuối danh sách", 22, ["thêm phần tử vào cuối danh sách"]),
    (23, "Toán tử in kiểm tra một giá trị có trong danh sách không", 23, ["toán tử in để kiểm tra"]),
    (23, "Lệnh insert chèn phần tử vào danh sách tại vị trí cho trước", 23, ["insert"]),
    (23, "Lệnh clear() và remove() xoá phần tử danh sách", 23, ["clear()"]),
    (24, "Xâu kí tự khác danh sách ở điểm nào, có thay đổi từng kí tự được không?", 24, ["không cho phép thay đổi từng kí tự"]),
    (24, "Duyệt từng kí tự của xâu bằng lệnh for", 24, ["duyệt từng kí tự của xâu"]),
    (24, "Kiểm tra một xâu có là xâu con của xâu khác", 24, ["xâu con"]),
    (25, "Lệnh find() tìm vị trí xâu con trong xâu mẹ", 25, ["find("]),
    (25, "Lệnh split() tách xâu thành danh sách các từ", 25, ["split("]),
    (25, "Lệnh join() nối danh sách các xâu thành một xâu", 25, ["join("]),
    (26, "Một số hàm thiết kế sẵn của Python", 26, ["Bảng 26.1"]),
    (26, "Cách thiết lập hàm tự định nghĩa với def và return", 26, ["từ khoá def"]),
    (26, "Hàm không trả lại giá trị viết thế nào?", 26, ["không trả lại giá trị"]),
    (27, "Phân biệt tham số và đối số của hàm", 27, ["đối số"]),
    (27, "Cách sử dụng chương trình con trong chương trình chính", 27, ["CÁCH SỬ DỤNG CHƯƠNG TRÌNH CON"]),
    (27, "Hàm tính tổng các số dương của một dãy", 27, ["tongduong"]),
    (28, "Biến khai báo bên trong hàm có phạm vi thế nào?", 28, ["tính địa phương"]),
    (28, "Từ khoá global dùng để làm gì?", 28, ["global"]),
    (28, "Phạm vi của biến khai báo ngoài hàm", 28, ["KHAI BÁO NGOÀI HÀM"]),
    (29, "Phân biệt lỗi cú pháp, lỗi ngoại lệ và lỗi lôgic", 29, ["lỗi lôgic"]),
    (29, "Lỗi ZeroDivisionError xảy ra khi nào?", 29, ["ZeroDivisionError"]),
    (29, "Một số lỗi ngoại lệ thường gặp như IndexError, NameError", 29, ["IndexError"]),
    # Truy vấn kế thừa: đáp án ở bài TRƯỚC bài mục tiêu (kiểm tra bộ lọc bai_so <= k vẫn cho phép)
    (24, "Nhắc lại cú pháp lệnh for với range", 20, ["Cú pháp của lệnh lặp với số lần biết"]),
    (27, "Nhập số nguyên từ bàn phím bằng int(input())", 18, ["Nếu cần nhập số nguyên"]),
    (26, "Dùng toán tử in duyệt danh sách", 23, ["toán tử in để kiểm tra"]),
]


def _n(t: str) -> str:
    return unicodedata.normalize("NFC", t).lower()


def build_queries() -> None:
    import pypdf

    rules = SyllabusRules.load()
    reader = pypdf.PdfReader(str(ensure_fixed_pdf()))
    out = []
    for i, (target, q, gold_bai, phrases) in enumerate(QUERY_SPECS, start=1):
        lo, hi = rules.sgk_pages(gold_bai)
        pages = []
        for p in range(lo, hi + 1):
            text = _n(" ".join(clean_line(l) for l in (reader.pages[p].extract_text() or "").splitlines()))
            if any(_n(ph) in text for ph in phrases):
                pages.append(p)
        if not pages:
            raise SystemExit(f"Q{i}: không tìm thấy cụm {phrases} trong Bài {gold_bai} – sửa cụm từ")
        out.append({"qid": f"Q{i:02d}", "query": q, "target_bai": target, "gold_bai": gold_bai,
                    "gold_pages": pages, "gold_phrases": phrases})
    path = QUERIES
    path.write_text(json.dumps({"_meta": {"source": str(FIXED_PDF.name), "n": len(out),
                                          "gold": "trang (trang in) chứa gold_phrases trong Bài gold_bai"}, "queries": out},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(out)} truy vấn → {path}")
    for o in out:
        print(o["qid"], o["target_bai"], o["gold_bai"], o["gold_pages"])


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
    ap.add_argument("--build-queries", action="store_true", help="Sinh lại eval/retrieval_queries.json từ QUERY_SPECS")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    if args.build_queries:
        build_queries()
        return 0
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
