import io
import json
from pathlib import Path

import pytest

docx = pytest.importorskip("docx")
from docx.oxml.ns import qn  # noqa: E402
from docx.shared import Cm, Pt  # noqa: E402

from src.schemas import LessonPlan5512, LessonRequest  # noqa: E402
from src.utils.docx_exporter import export_lesson_docx, save_lesson_docx  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
ANSWER_HEADING = "ĐÁP ÁN VÀ HƯỚNG DẪN CHẤM (dành cho GV)"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@pytest.fixture
def request_bai20():
    return LessonRequest.model_validate(load("request_bai20.json"))


@pytest.fixture
def plan_bai20():
    return LessonPlan5512.model_validate(load("plan_bai20_clean.json"))


def open_docx(data: bytes):
    return docx.Document(io.BytesIO(data))


def body_paragraph_texts(doc) -> list[str]:
    """Văn bản từng đoạn theo đúng thứ tự trong thân tài liệu (kể cả đoạn trong bảng)."""
    out = []
    for p in doc.element.body.iter(qn("w:p")):
        out.append("".join(t.text or "" for t in p.iter(qn("w:t"))))
    return out


def first_index(texts, needle):
    for i, t in enumerate(texts):
        if needle in t:
            return i
    return -1


def all_indices(texts, needle):
    return [i for i, t in enumerate(texts) if needle in t]


def iter_body_runs(doc):
    for p in doc.element.body.iter(qn("w:p")):
        for r in p.iter(qn("w:r")):
            if any((t.text or "") for t in r.iter(qn("w:t"))):
                yield p, r


def run_font(r):
    rpr = r.find(qn("w:rPr"))
    rfonts = None if rpr is None else rpr.find(qn("w:rFonts"))
    if rfonts is None:
        return None, None
    return rfonts.get(qn("w:ascii")), rfonts.get(qn("w:eastAsia"))


def run_size_half_points(r):
    sz = r.find(qn("w:rPr")).find(qn("w:sz"))
    return int(sz.get(qn("w:val"))) if sz is not None else None


def is_code_paragraph(p):
    ppr = p.find(qn("w:pPr"))
    shd = None if ppr is None else ppr.find(qn("w:shd"))
    return shd is not None and shd.get(qn("w:fill")) == "F2F2F2"


# ---------------------------------------------------------------------------
def test_export_returns_docx_bytes_and_save(tmp_path, request_bai20, plan_bai20):
    data = export_lesson_docx(request_bai20, plan_bai20)
    assert data[:2] == b"PK"
    out = save_lesson_docx(request_bai20, plan_bai20, tmp_path / "sub" / "bai20.docx",
                           school="THPT Chu Văn An", teacher="Nguyễn Văn A")
    assert out.exists() and out.stat().st_size > 5000
    texts = body_paragraph_texts(docx.Document(str(out)))
    assert "Trường: THPT Chu Văn An" in texts
    assert "Họ và tên giáo viên: Nguyễn Văn A" in texts


def test_vietnamese_header_and_sections_roundtrip(tmp_path, request_bai20, plan_bai20):
    path = save_lesson_docx(request_bai20, plan_bai20, tmp_path / "bai20.docx")
    texts = body_paragraph_texts(docx.Document(str(path)))
    for expected in [
        "Tổ: Tin học",
        "KẾ HOẠCH BÀI DẠY",
        "TÊN BÀI DẠY: BÀI 20. CÂU LỆNH LẶP FOR",
        "Môn học: Tin học; Lớp: 10",
        "Thời gian thực hiện: 1 tiết thực hành – 45 phút",
        "I. MỤC TIÊU",
        "1. Về kiến thức: Biết cú pháp và thực hiện được lệnh lặp for với range().",
        "2. Về năng lực:",
        "a) Năng lực chung: Tự chủ và tự học; Giao tiếp và hợp tác; Giải quyết vấn đề và sáng tạo.",
        "b) Năng lực Tin học: NLc: Giải quyết vấn đề với sự trợ giúp của máy tính.",
        "3. Về phẩm chất: Chăm chỉ, trách nhiệm.",
        "II. THIẾT BỊ DẠY HỌC VÀ HỌC LIỆU",
        "Phòng máy, Python IDLE, SGK/SBT Tin học 10, 3 phiếu học tập.",
        "III. TIẾN TRÌNH DẠY HỌC",
        "Hoạt động 1: Khởi động (5 phút)",
        "Hoạt động 2: Hình thành kiến thức (15 phút)",
        "Hoạt động 4: Vận dụng (10 phút)",
        "a) Mục tiêu: Biết cú pháp for và range()",
        "Nguồn: SGK Tin học 10 – Kết nối tri thức, Bài 20, Lệnh range(), tr.106",
        "PHỤ LỤC – PHIẾU HỌC TẬP",
        "PHIẾU HỌC TẬP SỐ 1 – NHÓM CƠ BẢN (Nhớ/Hiểu)",
        "PHIẾU HỌC TẬP SỐ 3 – NHÓM NÂNG CAO (Phân tích/Sáng tạo)",
        ANSWER_HEADING,
    ]:
        assert expected in texts, expected
    # Thứ tự các phần lớn
    order = [first_index(texts, h) for h in
             ("KẾ HOẠCH BÀI DẠY", "I. MỤC TIÊU", "II. THIẾT BỊ", "III. TIẾN TRÌNH",
              "PHỤ LỤC – PHIẾU HỌC TẬP", ANSWER_HEADING)]
    assert order == sorted(order) and -1 not in order


def test_activity_tables_have_four_steps(request_bai20, plan_bai20):
    doc = open_docx(export_lesson_docx(request_bai20, plan_bai20))
    step_tables = [t for t in doc.tables if t.cell(0, 0).text == "Hoạt động của GV – HS"]
    assert len(step_tables) == len(plan_bai20.activities) == 4
    for table, act in zip(step_tables, plan_bai20.activities):
        assert len(table.columns) == 2 and len(table.rows) == 5
        assert table.cell(0, 1).text == "Dự kiến sản phẩm"
        col1 = [table.cell(r, 0).text for r in range(1, 5)]
        assert col1 == [
            f"Bước 1. Chuyển giao nhiệm vụ: {act.step1_transfer}",
            f"Bước 2. Thực hiện nhiệm vụ: {act.step2_execute}",
            f"Bước 3. Báo cáo, thảo luận: {act.step3_report}",
            f"Bước 4. Kết luận, nhận định: {act.step4_conclude}",
        ]
        # Cột 2 được gộp dọc 4 dòng và chứa sản phẩm dự kiến
        merged = {table.cell(r, 1)._tc for r in range(1, 5)}
        assert len(merged) == 1
        assert table.cell(1, 1).text == act.expected_product
        assert table.style.name == "Table Grid"


def test_worksheets_show_starter_only_and_solutions_after_answer_heading(request_bai20, plan_bai20):
    doc = open_docx(export_lesson_docx(request_bai20, plan_bai20))
    texts = body_paragraph_texts(doc)
    answer_idx = first_index(texts, ANSWER_HEADING)
    appendix_idx = first_index(texts, "PHỤ LỤC – PHIẾU HỌC TẬP")
    assert 0 < appendix_idx < answer_idx

    starter_idx = first_index(texts, "for i in range(1, ___):")
    assert appendix_idx < starter_idx < answer_idx

    for sol_line in ("for i in range(1, n + 1):", "        dem = dem + 1",
                     "lon_nhat = int(input())", "for i in range(n - 1):"):
        idxs = all_indices(texts, sol_line)
        assert idxs, sol_line
        assert min(idxs) > answer_idx, sol_line

    # Indentation trong code được giữ nguyên
    assert "    S = S + i" in texts
    # Không còn rào ``` lọt ra văn bản
    assert not any("```" in t for t in texts)

    # Bảng test case
    tc_tables = [t for t in doc.tables if t.cell(0, 0).text == "STT"]
    assert len(tc_tables) == 3
    hdr = [c.text for c in tc_tables[0].rows[0].cells]
    assert hdr == ["STT", "Input", "Output mong đợi", "Biên?"]
    assert [c.text for c in tc_tables[0].rows[1].cells] == ["1", "5", "15", "Không"]
    assert [c.text for c in tc_tables[0].rows[3].cells] == ["3", "0", "0", "Có"]
    assert tc_tables[2].cell(1, 1).text == "4\n3\n9\n-2\n5"


def test_page_break_between_worksheets(request_bai20, plan_bai20):
    doc = open_docx(export_lesson_docx(request_bai20, plan_bai20))
    titles = [p for p in doc.paragraphs if p.text.startswith("PHIẾU HỌC TẬP SỐ")]
    assert [p.paragraph_format.page_break_before for p in titles] == [None, True, True]
    for heading in ("PHỤ LỤC – PHIẾU HỌC TẬP", ANSWER_HEADING):
        p = next(p for p in doc.paragraphs if p.text == heading)
        assert p.paragraph_format.page_break_before is True


def test_fonts_page_setup_and_footer(request_bai20, plan_bai20):
    doc = open_docx(export_lesson_docx(request_bai20, plan_bai20))
    sec = doc.sections[0]
    assert abs(sec.page_width - Cm(21)) < Cm(0.05) and abs(sec.page_height - Cm(29.7)) < Cm(0.05)
    assert abs(sec.left_margin - Cm(3)) < Cm(0.05)
    assert abs(sec.right_margin - Cm(2)) < Cm(0.05)

    normal = doc.styles["Normal"]
    assert normal.font.name == "Times New Roman"
    assert normal.font.size == Pt(13)
    assert normal.element.rPr.rFonts.get(qn("w:eastAsia")) == "Times New Roman"
    assert normal.paragraph_format.line_spacing == pytest.approx(1.15)

    n_code = 0
    for p, r in iter_body_runs(doc):
        ascii_font, ea_font = run_font(r)
        assert ascii_font == ea_font, "eastAsia font phải trùng font chính"
        if is_code_paragraph(p):
            n_code += 1
            assert ascii_font == "Consolas"
            assert run_size_half_points(r) == 22  # 11pt
        else:
            assert ascii_font in ("Times New Roman", "Consolas")
    assert n_code > 10

    title_run = next(r for p in doc.paragraphs if p.text == "KẾ HOẠCH BÀI DẠY" for r in p.runs)
    assert title_run.font.name == "Times New Roman" and title_run.bold

    footer_xml = sec.footer._element.xml
    assert "PAGE" in footer_xml and "fldChar" in footer_xml


def test_fenced_code_in_activity_rendered_monospace(request_bai20, plan_bai20):
    plan = plan_bai20.model_copy(deep=True)
    plan.activities[1].content = (
        "Ví dụ minh họa:\n```python\nfor i in range(3):\n    print(i)\n```\nKết thúc ví dụ."
    )
    plan.activities[1].step2_execute = "HS gõ lệnh:\n```\nfor k in range(2):\n    print('Xin chào')\n```"
    doc = open_docx(export_lesson_docx(request_bai20, plan))
    texts = body_paragraph_texts(doc)
    assert "b) Nội dung:" in texts and "Ví dụ minh họa:" in texts and "Kết thúc ví dụ." in texts
    assert not any("```" in t for t in texts)
    code_pars = {
        "".join(t.text for t in p.iter(qn("w:t"))): p
        for p in doc.element.body.iter(qn("w:p")) if is_code_paragraph(p)
    }
    assert "    print(i)" in code_pars and "for i in range(3):" in code_pars
    assert "    print('Xin chào')" in code_pars  # code bên trong ô bảng
    t = next(code_pars["    print(i)"].iter(qn("w:t")))
    assert t.get(qn("xml:space")) == "preserve"


def test_verification_line(request_bai20, plan_bai20):
    verification = [
        {"tier": "Cơ bản", "ast_pass": True, "sandbox_pass": True, "n_passed": 3, "total": 3},
        {"tier": "Đạt chuẩn", "ast_pass": True, "sandbox_pass": False, "n_passed": 2, "total": 3},
        {"tier": "Nâng cao", "ast_pass": False, "sandbox_pass": False, "n_passed": 0, "total": 3},
    ]
    texts = body_paragraph_texts(open_docx(export_lesson_docx(request_bai20, plan_bai20, verification)))
    answer_idx = first_index(texts, ANSWER_HEADING)
    lines = [t for t in texts[answer_idx:] if t.startswith("Kiểm định:")]
    assert lines == [
        "Kiểm định: AST PASS · Sandbox 3/3",
        "Kiểm định: AST PASS · Sandbox 2/3",
        "Kiểm định: AST FAIL · Sandbox 0/3",
    ]
    # Không có verification → không in dòng kiểm định
    texts = body_paragraph_texts(open_docx(export_lesson_docx(request_bai20, plan_bai20)))
    assert not any(t.startswith("Kiểm định:") for t in texts)


@pytest.mark.parametrize(
    "mode, expected",
    [
        ("45m_theory", "Thời gian thực hiện: 1 tiết lý thuyết – 45 phút"),
        ("45m_practice", "Thời gian thực hiện: 1 tiết thực hành – 45 phút"),
        ("90m_full", "Thời gian thực hiện: 2 tiết – 90 phút"),
    ],
)
def test_time_line_from_scope_mode(plan_bai20, mode, expected):
    req = LessonRequest(target_bai=20, topic_name="Câu lệnh lặp for", scope_mode=mode,
                        target_minutes=90 if mode == "90m_full" else 45)
    texts = body_paragraph_texts(open_docx(export_lesson_docx(req, plan_bai20)))
    assert expected in texts
    if mode == "90m_full":
        assert any("2 tiết" in t for t in texts)


def test_empty_plan_without_worksheets_exports(tmp_path):
    req = LessonRequest(target_bai=16)
    plan = LessonPlan5512()
    data = export_lesson_docx(req, plan)
    doc = open_docx(data)
    texts = body_paragraph_texts(doc)
    assert "TÊN BÀI DẠY: BÀI 16" in texts
    assert "PHỤ LỤC – PHIẾU HỌC TẬP" in texts and ANSWER_HEADING in texts
    assert "(Không có phiếu học tập)" in texts
    assert doc.tables == []

    # Hoạt động rỗng (chỉ có tên + thời lượng) vẫn ra đủ bảng 4 bước
    plan2 = LessonPlan5512.model_validate(
        {"activities": [{"activity_name": "", "duration_minutes": 0}],
         "worksheets": [{"tier": ""}]}
    )
    doc2 = open_docx(export_lesson_docx(req, plan2, verification=[{}]))
    t = doc2.tables[0]
    assert [t.cell(r, 0).text for r in range(1, 5)] == [
        "Bước 1. Chuyển giao nhiệm vụ:", "Bước 2. Thực hiện nhiệm vụ:",
        "Bước 3. Báo cáo, thảo luận:", "Bước 4. Kết luận, nhận định:",
    ]
    assert "PHIẾU HỌC TẬP SỐ 1" in body_paragraph_texts(doc2)


def test_accepts_dict_inputs():
    data = export_lesson_docx(load("request_bai20.json"), load("plan_bai20_clean.json"))
    assert "KẾ HOẠCH BÀI DẠY" in body_paragraph_texts(open_docx(data))
