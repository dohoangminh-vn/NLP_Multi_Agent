"""Sinh eval/retrieval_queries.json (TV2): câu hỏi tự nhiên của GV + nhãn trang đúng.

Nhãn trang KHÔNG gán tay: mỗi câu hỏi có `gold_phrases` (cụm từ nguyên văn SGK chứa câu trả lời);
trang đúng = các trang (trong dải trang của bài gold) có chứa cụm từ đó trong text PDF đã sửa.
→ Ai cũng kiểm lại được nhãn, độc lập với cách chunk.

    python scripts/build_retrieval_queries.py
"""

from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.rag.ingest import FIXED_PDF, clean_line, ensure_fixed_pdf  # noqa: E402
from src.tools.ast_guard import SyllabusRules  # noqa: E402

# (target_bai, câu hỏi, bài chứa đáp án, [cụm từ SGK])
QUERIES = [
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


def main() -> None:
    import pypdf

    rules = SyllabusRules.load()
    reader = pypdf.PdfReader(str(ensure_fixed_pdf()))
    out = []
    for i, (target, q, gold_bai, phrases) in enumerate(QUERIES, start=1):
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
    path = ROOT / "eval" / "retrieval_queries.json"
    path.write_text(json.dumps({"_meta": {"source": str(FIXED_PDF.name), "n": len(out),
                                          "gold": "trang (trang in) chứa gold_phrases trong Bài gold_bai"}, "queries": out},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(out)} truy vấn → {path}")
    for o in out:
        print(o["qid"], o["target_bai"], o["gold_bai"], o["gold_pages"])


if __name__ == "__main__":
    main()
