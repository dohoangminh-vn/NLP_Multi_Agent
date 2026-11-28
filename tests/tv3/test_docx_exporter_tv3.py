import io
import copy
import pytest
from docx import Document
from src.utils.docx_exporter import export_lesson_docx, export_student_docx, save_lesson_docx
from src.utils.pedagogy_validator import PedagogyValidationError
from test_detail_designer import design, REQ


def texts(data):
    doc = Document(io.BytesIO(data))
    return '\n'.join(doc.element.body.xpath('.//w:t/text()'))


def test_teacher_export():
    plan = design()
    data = export_lesson_docx(REQ, plan, school='THPT mẫu', teacher='GV mẫu')
    content = texts(data)
    assert 'THPT mẫu' in content and 'GV mẫu' in content
    assert all(x in content for x in ['I. MỤC TIÊU', 'III. TIẾN TRÌNH DẠY HỌC', 'ĐÁP ÁN VÀ HƯỚNG DẪN CHẤM', 'Bước 4. Kết luận'])
    assert len(Document(io.BytesIO(data)).tables) == 7


def test_student_separate():
    plan = design()
    plan['worksheets'][0]['solution_code'] = 'SECRET_SOLUTION_MARKER'
    plan['worksheets'][0]['test_cases'][0]['expected_stdout'] = 'SECRET_TEST_MARKER'
    content = texts(export_student_docx(REQ, plan))
    assert 'SECRET_SOLUTION_MARKER' not in content
    assert 'SECRET_TEST_MARKER' not in content
    assert 'ĐÁP ÁN VÀ HƯỚNG DẪN CHẤM' not in content
    assert content.count('PHIẾU HỌC TẬP SỐ') == 3
    assert 'Chương trình khung' in content


def test_scope_invalid():
    with pytest.raises(PedagogyValidationError):
        export_lesson_docx(dict(REQ,target_minutes=90), design())


def test_unknown_verification():
    content = texts(export_lesson_docx(REQ, design(), [{}]))
    assert 'Chưa xác định' in content


def test_save(tmp_path):
    path = save_lesson_docx(REQ, design(), tmp_path / 'sub' / 'lesson.docx')
    assert path.exists() and path.read_bytes().startswith(b'PK')


def test_unchanged_inputs():
    plan = design(); before = copy.deepcopy(plan)
    export_lesson_docx(REQ, plan); export_student_docx(REQ, plan)
    assert plan == before
