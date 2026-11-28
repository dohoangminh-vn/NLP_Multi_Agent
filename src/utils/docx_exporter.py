"""Xuất Kế hoạch bài dạy ra Word (.docx) theo Phụ lục IV – Công văn 5512/BGDĐT-GDTrH.

UC-05 / TV3 nhiệm vụ 4. Dùng `python-docx`.

Bố cục:
    Trường / Tổ / Họ và tên GV  →  KẾ HOẠCH BÀI DẠY  →  Tên bài, Môn/Lớp, Thời gian
    I.   MỤC TIÊU (Kiến thức · Năng lực chung + Năng lực Tin học · Phẩm chất)
    II.  THIẾT BỊ DẠY HỌC VÀ HỌC LIỆU
    III. TIẾN TRÌNH DẠY HỌC (mỗi hoạt động: a) Mục tiêu b) Nội dung c) Sản phẩm
         d) Tổ chức thực hiện = bảng 2 cột, đủ 4 bước) + dòng trích dẫn nguồn SGK
    PHỤ LỤC – PHIẾU HỌC TẬP (chỉ in `starter_code`, mỗi phiếu một trang)
    ĐÁP ÁN VÀ HƯỚNG DẪN CHẤM (dành cho GV): `solution_code` + bảng test case
         + kết quả kiểm định AST/Sandbox (nếu có).

Các trường văn bản có thể chứa xuống dòng và khối code ```python … ```; khối code
được in bằng Consolas 11pt trong khung nền xám nhạt, giữ nguyên thụt lề.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Optional, Union

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

# TV3: kế thừa Exporter từ branch khanh (a120dc4), giữ API cũ.
from types import SimpleNamespace
from typing import TYPE_CHECKING
from src.utils.pedagogy_validator import validate_scope, validate_lesson_number

if TYPE_CHECKING:
    from src.schemas import Activity5512, LessonPlan5512, LessonRequest, WorksheetItem

__all__ = ["export_lesson_docx", "save_lesson_docx", "export_student_docx", "build_lesson_document"]

BODY_FONT = "Times New Roman"
BODY_SIZE = Pt(13)
CODE_FONT = "Consolas"
CODE_SIZE = Pt(11)
SMALL_SIZE = Pt(11)
CODE_FILL = "F2F2F2"
HEADER_FILL = "D9D9D9"
LINE_SPACING = 1.15
PLACEHOLDER = "…………………………"

SUBJECT = "Tin học"
GRADE = 10
TEACHING_GROUP = "Tin học"

# scope_mode → (mô tả số tiết, số phút)
SCOPE_LABELS = {
    "45m_theory": ("1 tiết lý thuyết", 45),
    "45m_practice": ("1 tiết thực hành", 45),
    "90m_full": ("2 tiết", 90),
}

STEP_LABELS = (
    ("Bước 1. Chuyển giao nhiệm vụ:", "step1_transfer"),
    ("Bước 2. Thực hiện nhiệm vụ:", "step2_execute"),
    ("Bước 3. Báo cáo, thảo luận:", "step3_report"),
    ("Bước 4. Kết luận, nhận định:", "step4_conclude"),
)

_FENCE_RE = re.compile(r"^\s*```")

# Thứ tự phần tử con theo schema OOXML (Word báo lỗi file nếu sai thứ tự).
_PPR_AFTER_SHD = (
    "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap", "w:overflowPunct",
    "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN", "w:bidi", "w:adjustRightInd",
    "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing", "w:mirrorIndents",
    "w:suppressOverlap", "w:jc", "w:textDirection", "w:textAlignment", "w:textboxTightWrap",
    "w:outlineLvl", "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange",
)
_TCPR_AFTER_SHD = (
    "w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign", "w:hideMark",
    "w:headers", "w:cellIns", "w:cellDel", "w:cellMerge", "w:tcPrChange",
)
_RPR_AFTER_NOPROOF = (
    "w:snapToGrid", "w:vanish", "w:webHidden", "w:color", "w:spacing", "w:w", "w:kern",
    "w:position", "w:sz", "w:szCs", "w:highlight", "w:u", "w:effect", "w:bdr", "w:shd",
    "w:fitText", "w:vertAlign", "w:rtl", "w:cs", "w:em", "w:lang", "w:eastAsianLayout",
    "w:specVanish", "w:oMath",
)


# ---------------------------------------------------------------------------
# Tiện ích XML / định dạng
# ---------------------------------------------------------------------------
def _set_rfonts(rpr, font_name: str) -> None:
    """Đặt font cho mọi bảng mã (ascii, hAnsi, eastAsia, cs) để tiếng Việt hiển thị đúng."""
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(attr), font_name)
    # Bỏ font theo theme (nếu có) để không bị ghi đè.
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        if rfonts.get(qn(attr)) is not None:
            del rfonts.attrib[qn(attr)]


def _style_run(run, *, font: str = BODY_FONT, size=BODY_SIZE, bold=None, italic=None, code=False):
    run.font.name = font
    run.font.size = size
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic
    rpr = run._element.get_or_add_rPr()
    _set_rfonts(rpr, font)
    if code:
        # w:noProof phải đứng trước color/sz… theo thứ tự schema CT_RPr.
        rpr.insert_element_before(OxmlElement("w:noProof"), *_RPR_AFTER_NOPROOF)
    return run


def _add_run(par, text: str, **kw):
    return _style_run(par.add_run(text), **kw)


def _shade(element_pr, fill: str) -> None:
    """Thêm nền (w:shd) vào pPr hoặc tcPr, đúng vị trí theo schema OOXML."""
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    successors = _PPR_AFTER_SHD if element_pr.tag == qn("w:pPr") else _TCPR_AFTER_SHD
    element_pr.insert_element_before(shd, *successors)


def _code_border(ppr) -> None:
    """Viền mảnh quanh khung code; các đoạn liên tiếp cùng viền được Word gộp thành một khung."""
    bdr = OxmlElement("w:pBdr")
    for side in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:space"), "4")
        el.set(qn("w:color"), "BFBFBF")
        bdr.append(el)
    ppr.insert_element_before(bdr, "w:shd", *_PPR_AFTER_SHD)


def _is_cell(container) -> bool:
    return container.__class__.__name__ == "_Cell"


def _new_par(container, align=None, space_after=None):
    """Lấy đoạn mới; với ô bảng thì tái sử dụng đoạn rỗng mặc định đầu tiên."""
    if _is_cell(container):
        pars = container.paragraphs
        if len(pars) == 1 and not pars[0].runs and not pars[0].text:
            par = pars[0]
        else:
            par = container.add_paragraph()
        par.paragraph_format.space_after = Pt(2) if space_after is None else space_after
    else:
        par = container.add_paragraph()
        if space_after is not None:
            par.paragraph_format.space_after = space_after
    if align is not None:
        par.alignment = align
    return par


def _split_segments(text: str) -> list[tuple[str, list[str]]]:
    """Tách văn bản thành các đoạn ('text', lines) và ('code', lines) theo rào ```."""
    segments: list[tuple[str, list[str]]] = []
    kind, buf = "text", []

    def flush():
        nonlocal buf
        if kind == "code":
            while buf and not buf[0].strip():
                buf.pop(0)
            while buf and not buf[-1].strip():
                buf.pop()
        if buf:
            segments.append((kind, buf))
        buf = []

    for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _FENCE_RE.match(line):
            flush()
            kind = "text" if kind == "code" else "code"
            continue
        if kind == "code" and line.rstrip().endswith("```"):
            buf.append(line.rstrip()[:-3])
            flush()
            kind = "text"
            continue
        buf.append(line)
    flush()
    return segments


def _add_code_block(container, code: str | list[str]) -> None:
    lines = code.replace("\r\n", "\n").split("\n") if isinstance(code, str) else list(code)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        lines = [""]
    for idx, line in enumerate(lines):
        par = _new_par(container, space_after=Pt(0))
        fmt = par.paragraph_format
        fmt.space_before = Pt(4) if idx == 0 else Pt(0)
        fmt.space_after = Pt(4) if idx == len(lines) - 1 else Pt(0)
        fmt.line_spacing = 1.0
        if len(lines) <= 20 and idx < len(lines) - 1:
            fmt.keep_with_next = True
        fmt.left_indent = Cm(0.2)
        fmt.right_indent = Cm(0.2)
        ppr = par._p.get_or_add_pPr()
        _code_border(ppr)
        _shade(ppr, CODE_FILL)
        line = line.replace("\t", "    ")
        if line:
            # python-docx tự thêm xml:space="preserve" khi có khoảng trắng đầu/cuối.
            _add_run(par, line, font=CODE_FONT, size=CODE_SIZE, code=True)


def _add_rich_text(container, text: str, *, italic=None, size=BODY_SIZE, placeholder=True) -> None:
    """In văn bản nhiều dòng + khối code ```…```."""
    segments = _split_segments(text)
    if not segments:
        if placeholder:
            _add_run(_new_par(container), PLACEHOLDER, size=size, italic=italic)
        return
    for kind, lines in segments:
        if kind == "code":
            _add_code_block(container, lines)
            continue
        for line in lines:
            if not line.strip():
                continue
            _add_run(_new_par(container), line.rstrip(), size=size, italic=italic)


def _has_block_content(text: str) -> bool:
    t = (text or "").strip()
    return "\n" in t or "```" in t


def _labeled(container, label: str, text: str, *, label_italic=False) -> None:
    """'label nội dung' trên một dòng nếu ngắn gọn, ngược lại nhãn rồi khối nội dung."""
    par = _new_par(container)
    _add_run(par, label, bold=True, italic=label_italic or None)
    body = (text or "").strip()
    if not _has_block_content(body):
        _add_run(par, " " + (body or PLACEHOLDER))
        return
    _add_rich_text(container, body)


def _heading(doc, text: str, *, center=False, size=BODY_SIZE, space_before=Pt(6), page_break=False):
    par = doc.add_paragraph()
    if page_break:
        par.paragraph_format.page_break_before = True
    par.paragraph_format.space_before = space_before
    par.paragraph_format.keep_with_next = True
    if center:
        par.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _add_run(par, text, bold=True, size=size)
    return par


def _set_cell_width(cell, width) -> None:
    cell.width = width
    tcpr = cell._tc.get_or_add_tcPr()
    tcw = tcpr.find(qn("w:tcW"))
    if tcw is None:
        tcw = OxmlElement("w:tcW")
        tcpr.append(tcw)
    tcw.set(qn("w:w"), str(int(width.twips)))
    tcw.set(qn("w:type"), "dxa")


def _mark_header_row(row) -> None:
    trpr = row._tr.get_or_add_trPr()
    hdr = OxmlElement("w:tblHeader")
    trpr.append(hdr)
    for cell in row.cells:
        _shade(cell._tc.get_or_add_tcPr(), HEADER_FILL)


def _make_table(doc, n_rows: int, widths: list) -> object:
    table = doc.add_table(rows=n_rows, cols=len(widths))
    table.style = "Table Grid"
    # Viền tường minh (không chỉ dựa vào style) để mọi trình đọc đều hiển thị khung bảng.
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), "000000")
        borders.append(el)
    table._tbl.tblPr.insert_element_before(
        borders, "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook", "w:tblCaption", "w:tblDescription"
    )
    cell_mar = OxmlElement("w:tblCellMar")
    for side in ("left", "right"):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:w"), "85")  # ~0,15 cm
        el.set(qn("w:type"), "dxa")
        cell_mar.append(el)
    table._tbl.tblPr.insert_element_before(cell_mar, "w:tblLook", "w:tblCaption", "w:tblDescription")
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    for row in table.rows:
        for cell, w in zip(row.cells, widths):
            _set_cell_width(cell, w)
    grid = table._tbl.tblGrid
    for gc, w in zip(grid.findall(qn("w:gridCol")), widths):
        gc.set(qn("w:w"), str(int(w.twips)))
    return table


def _header_cell(cell, text: str) -> None:
    par = _new_par(cell, align=WD_ALIGN_PARAGRAPH.CENTER)
    _add_run(par, text, bold=True)


def _add_page_number_footer(section) -> None:
    par = section.footer.paragraphs[0] if section.footer.paragraphs else section.footer.add_paragraph()
    par.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = _add_run(par, "", size=SMALL_SIZE)
    for tag, extra in (("w:fldChar", "begin"), ("w:instrText", " PAGE "), ("w:fldChar", "separate")):
        el = OxmlElement(tag)
        if tag == "w:fldChar":
            el.set(qn("w:fldCharType"), extra)
        else:
            el.set(qn("xml:space"), "preserve")
            el.text = extra
        run._r.append(el)
    num = _add_run(par, "1", size=SMALL_SIZE)
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    num._r.append(end)


# ---------------------------------------------------------------------------
# Thiết lập tài liệu
# ---------------------------------------------------------------------------
def _setup_document(request: LessonRequest, teacher: str):
    doc = Document()
    sec = doc.sections[0]
    sec.orientation = WD_ORIENT.PORTRAIT
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.top_margin = sec.bottom_margin = Cm(2.0)
    sec.left_margin, sec.right_margin = Cm(3.0), Cm(2.0)
    sec.header_distance = sec.footer_distance = Cm(1.0)

    normal = doc.styles["Normal"]
    normal.font.name = BODY_FONT
    normal.font.size = BODY_SIZE
    normal.font.color.rgb = RGBColor(0, 0, 0)
    rpr = normal.element.get_or_add_rPr()
    _set_rfonts(rpr, BODY_FONT)
    lang = OxmlElement("w:lang")
    lang.set(qn("w:val"), "vi-VN")
    lang.set(qn("w:eastAsia"), "vi-VN")
    rpr.append(lang)
    pf = normal.paragraph_format
    pf.line_spacing = LINE_SPACING
    pf.space_before = Pt(0)
    pf.space_after = Pt(3)

    # Font mặc định của tài liệu (docDefaults) cũng là Times New Roman.
    defaults = doc.styles.element.find(qn("w:docDefaults"))
    if defaults is not None:
        rpr_default = defaults.find(qn("w:rPrDefault"))
        if rpr_default is not None:
            inner = rpr_default.find(qn("w:rPr"))
            if inner is not None:
                _set_rfonts(inner, BODY_FONT)

    props = doc.core_properties
    props.title = f"Kế hoạch bài dạy – Bài {request.target_bai}. {request.topic_name}".strip()
    props.author = teacher or "Multi-Agent Lesson Planner"
    props.subject = f"{SUBJECT} {GRADE}"
    props.language = "vi-VN"

    _add_page_number_footer(sec)
    return doc


def _time_line(request: LessonRequest) -> str:
    label, minutes = SCOPE_LABELS.get(request.scope_mode, ("1 tiết", request.target_minutes or 45))
    return f"Thời gian thực hiện: {label} – {minutes} phút"


# ---------------------------------------------------------------------------
# Các phần của giáo án
# ---------------------------------------------------------------------------
def _write_header(doc, request: LessonRequest, school: str, teacher: str) -> None:
    for label, value in (
        ("Trường: ", school),
        ("Tổ: ", TEACHING_GROUP),
        ("Họ và tên giáo viên: ", teacher),
    ):
        par = _new_par(doc, space_after=Pt(0))
        _add_run(par, label, bold=True)
        _add_run(par, value.strip() or PLACEHOLDER)

    _heading(doc, "KẾ HOẠCH BÀI DẠY", center=True, size=Pt(14), space_before=Pt(18))
    topic = (request.topic_name or "").strip().upper()
    title = f"TÊN BÀI DẠY: BÀI {request.target_bai}" + (f". {topic}" if topic else "")
    _heading(doc, title, center=True, space_before=Pt(6))
    for line in (f"Môn học: {SUBJECT}; Lớp: {GRADE}", _time_line(request)):
        par = _new_par(doc, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=Pt(0))
        _add_run(par, line)
    doc.add_paragraph()


def _write_objectives(doc, plan: LessonPlan5512) -> None:
    _heading(doc, "I. MỤC TIÊU")
    _labeled(doc, "1. Về kiến thức:", plan.knowledge_goals)
    par = _new_par(doc)
    _add_run(par, "2. Về năng lực:", bold=True)
    _labeled(doc, "a) Năng lực chung:", plan.general_competencies, label_italic=True)
    _labeled(doc, "b) Năng lực Tin học:", plan.cs_competencies, label_italic=True)
    _labeled(doc, "3. Về phẩm chất:", plan.qualities)

    _heading(doc, "II. THIẾT BỊ DẠY HỌC VÀ HỌC LIỆU")
    _add_rich_text(doc, plan.teaching_materials)


def _citation_line(c) -> str:
    parts = [c.doc_name]
    if c.bai_so:
        parts.append(f"Bài {c.bai_so}")
    if c.section_title:
        parts.append(c.section_title)
    if c.page_number:
        parts.append(f"tr.{c.page_number}")
    return "Nguồn: " + ", ".join(p for p in parts if p)


def _write_activity(doc, idx: int, act: Activity5512) -> None:
    name = act.activity_name.strip() or "(chưa đặt tên)"
    _heading(doc, f"Hoạt động {idx}: {name} ({act.duration_minutes} phút)", space_before=Pt(8))
    _labeled(doc, "a) Mục tiêu:", act.goal)
    _labeled(doc, "b) Nội dung:", act.content)
    _labeled(doc, "c) Sản phẩm:", act.expected_product)
    par = _new_par(doc)
    par.paragraph_format.keep_with_next = True
    _add_run(par, "d) Tổ chức thực hiện:", bold=True)

    table = _make_table(doc, 1 + len(STEP_LABELS), [Cm(10.0), Cm(6.0)])
    _mark_header_row(table.rows[0])
    _header_cell(table.cell(0, 0), "Hoạt động của GV – HS")
    _header_cell(table.cell(0, 1), "Dự kiến sản phẩm")
    for r, (label, field) in enumerate(STEP_LABELS, start=1):
        cell = table.cell(r, 0)
        text = (getattr(act, field) or "").strip()
        par = _new_par(cell)
        _add_run(par, label, bold=True)
        if text and not _has_block_content(text):
            _add_run(par, " " + text)
        elif text:
            _add_rich_text(cell, text)
    product_cell = table.cell(1, 1).merge(table.cell(len(STEP_LABELS), 1))
    _add_rich_text(product_cell, act.expected_product)
    # Keep short activity tables together to avoid a vertically merged cell
    # stretching across pages. Long tables remain splittable.
    if sum(len(getattr(act, field) or "") for _, field in STEP_LABELS) + len(act.expected_product) < 900:
        for row in table.rows[:-1]:
            for cell in row.cells:
                for p in cell.paragraphs:
                    p.paragraph_format.keep_with_next = True
    for j, c in enumerate(act.citations):
        par = _new_par(doc, space_after=Pt(0))
        if j == 0:
            par.paragraph_format.space_before = Pt(3)
        _add_run(par, _citation_line(c), italic=True, size=SMALL_SIZE)


def _write_process(doc, plan: LessonPlan5512) -> None:
    _heading(doc, "III. TIẾN TRÌNH DẠY HỌC")
    if not plan.activities:
        _add_rich_text(doc, "")
        return
    for i, act in enumerate(plan.activities, start=1):
        _write_activity(doc, i, act)


def _worksheet_title(k: int, ws: WorksheetItem) -> str:
    tier = (ws.tier or "").strip().upper()
    title = f"PHIẾU HỌC TẬP SỐ {k}" + (f" – NHÓM {tier}" if tier else "")
    if ws.bloom_level.strip():
        title += f" ({ws.bloom_level.strip()})"
    return title


def _write_worksheets(doc, plan: LessonPlan5512, *, page_break=True) -> None:
    _heading(doc, "PHỤ LỤC – PHIẾU HỌC TẬP", center=True, page_break=page_break)
    if not plan.worksheets:
        par = _new_par(doc)
        _add_run(par, "(Không có phiếu học tập)", italic=True)
        return
    for k, ws in enumerate(plan.worksheets, start=1):
        _heading(doc, _worksheet_title(k, ws), center=True, page_break=k > 1, space_before=Pt(10))
        par = _new_par(doc, space_after=Pt(8))
        _add_run(par, "Họ và tên: ………………………………………  Lớp: …………")
        _labeled(doc, "Đề bài:", ws.prompt_text)
        if ws.starter_code.strip():
            par = _new_par(doc)
            par.paragraph_format.keep_with_next = True
            _add_run(par, "Chương trình khung:", bold=True)
            _add_code_block(doc, ws.starter_code)


def _find_verification(verification: Optional[list[dict]], idx: int, ws: WorksheetItem) -> Optional[dict]:
    if not verification:
        return None
    tier = (ws.tier or "").strip().casefold()
    for v in verification:
        if str(v.get("tier", "")).strip().casefold() == tier and tier:
            return v
    if idx < len(verification) and not verification[idx].get("tier"):
        return verification[idx]
    return None


def _verification_line(v: dict) -> str:
    ast = {True: "PASS", False: "FAIL"}.get(v.get("ast_pass"), "Chưa xác định")
    n, total = v.get("n_passed"), v.get("total")
    if n is not None and total is not None:
        sandbox = f"{n}/{total}"
    else:
        sandbox = {True: "PASS", False: "FAIL"}.get(v.get("sandbox_pass"), "Chưa xác định")
    return f"Kiểm định: AST {ast} · Sandbox {sandbox}"


def _code_cell(cell, text: str) -> None:
    if not text:
        _add_run(_new_par(cell), "(rỗng)", italic=True, size=SMALL_SIZE)
        return
    for line in text.replace("\r\n", "\n").split("\n"):
        par = _new_par(cell, space_after=Pt(0))
        par.paragraph_format.line_spacing = 1.0
        if line:
            _add_run(par, line, font=CODE_FONT, size=CODE_SIZE, code=True)


def _write_test_table(doc, ws: WorksheetItem) -> None:
    widths = [Cm(1.3), Cm(5.7), Cm(5.7), Cm(3.3)]
    table = _make_table(doc, 1 + len(ws.test_cases), widths)
    _mark_header_row(table.rows[0])
    for col, label in enumerate(("STT", "Input", "Output mong đợi", "Biên?")):
        _header_cell(table.cell(0, col), label)
    for r, tc in enumerate(ws.test_cases, start=1):
        _add_run(_new_par(table.cell(r, 0), align=WD_ALIGN_PARAGRAPH.CENTER), str(r))
        _code_cell(table.cell(r, 1), tc.stdin_data)
        _code_cell(table.cell(r, 2), tc.expected_stdout)
        _add_run(_new_par(table.cell(r, 3), align=WD_ALIGN_PARAGRAPH.CENTER),
                 "Có" if tc.is_edge_case else "Không")


def _write_answer_key(doc, plan: LessonPlan5512, verification: Optional[list[dict]]) -> None:
    _heading(doc, "ĐÁP ÁN VÀ HƯỚNG DẪN CHẤM (dành cho GV)", center=True, page_break=True)
    if not plan.worksheets:
        par = _new_par(doc)
        _add_run(par, "(Không có phiếu học tập)", italic=True)
        return
    for k, ws in enumerate(plan.worksheets, start=1):
        tier = ws.tier.strip()
        _heading(doc, f"Phiếu học tập số {k}" + (f" – Nhóm {tier}" if tier else ""), space_before=Pt(10))
        par = _new_par(doc)
        par.paragraph_format.keep_with_next = True
        _add_run(par, "Lời giải tham khảo:", bold=True)
        if ws.solution_code.strip():
            _add_code_block(doc, ws.solution_code)
        else:
            _add_run(par, " " + PLACEHOLDER)

        v = _find_verification(verification, k - 1, ws)
        if v is not None:
            par = _new_par(doc)
            _add_run(par, _verification_line(v), italic=True, size=SMALL_SIZE)

        if ws.test_cases:
            par = _new_par(doc)
            par.paragraph_format.keep_with_next = True
            _add_run(par, "Bộ test case:", bold=True)
            _write_test_table(doc, ws)
            par = _new_par(doc)
            par.paragraph_format.space_before = Pt(3)
            _add_run(
                par,
                "Hướng dẫn chấm: chương trình cho kết quả đúng trên tất cả test case "
                "(kể cả trường hợp biên) đạt điểm tối đa; chấm theo tỉ lệ test case đạt.",
                italic=True,
                size=SMALL_SIZE,
            )


# ---------------------------------------------------------------------------
# API công khai
# ---------------------------------------------------------------------------
def _coerce(request, plan):
    # The shared schema is optional on the independent TV3 branch.
    try:
        from src.schemas import LessonRequest, LessonPlan5512
    except ModuleNotFoundError as exc:
        if exc.name != "src.schemas":
            raise
        def convert(value):
            if isinstance(value, dict):
                return SimpleNamespace(**{k: convert(v) for k, v in value.items()})
            if isinstance(value, list):
                return [convert(v) for v in value]
            return value
        if isinstance(request, dict):
            request = convert({"topic_name": "", **request})
        if isinstance(plan, dict):
            plan = convert(plan)
    else:
        if isinstance(request, dict):
            request = LessonRequest.model_validate(request)
        if isinstance(plan, dict):
            plan = LessonPlan5512.model_validate(plan)
    validate_lesson_number(request.target_bai)
    validate_scope(request.scope_mode, request.target_minutes)
    return request, plan


def build_lesson_document(
    request: LessonRequest,
    plan: LessonPlan5512,
    verification: list[dict] | None = None,
    school: str = "",
    teacher: str = "",
):
    """Dựng đối tượng `docx.Document` (dùng khi cần chỉnh thêm trước khi lưu)."""
    request, plan = _coerce(request, plan)
    doc = _setup_document(request, teacher)
    _write_header(doc, request, school, teacher)
    _write_objectives(doc, plan)
    _write_process(doc, plan)
    _write_worksheets(doc, plan)
    _write_answer_key(doc, plan, verification)
    return doc


def export_lesson_docx(
    request: LessonRequest,
    plan: LessonPlan5512,
    verification: list[dict] | None = None,
    school: str = "",
    teacher: str = "",
) -> bytes:
    """Trả về nội dung file .docx (bytes) – dùng cho `GET /api/lesson/{thread_id}/export`."""
    doc = build_lesson_document(request, plan, verification, school, teacher)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def save_lesson_docx(
    request: LessonRequest,
    plan: LessonPlan5512,
    path: Union[str, Path],
    verification: list[dict] | None = None,
    school: str = "",
    teacher: str = "",
) -> Path:
    """Ghi file .docx ra `path` và trả về đường dẫn."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(export_lesson_docx(request, plan, verification, school, teacher))
    return out


def export_student_docx(request, plan) -> bytes:
    """Export only worksheet prompts and starter code, not teacher answers.

    Does not certify that generated prompt text itself contains no hints/answers.
    """
    request, plan = _coerce(request, plan)
    doc = _setup_document(request, "")
    doc.core_properties.title = f"Phiếu học tập – Bài {request.target_bai}"
    _heading(doc, f"BÀI {request.target_bai}. {request.topic_name.upper()}", center=True)
    # The appendix heading must not force an otherwise empty first page.
    _write_worksheets(doc, plan, page_break=False)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
