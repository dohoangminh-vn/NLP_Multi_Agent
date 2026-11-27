"""Kiểm định ngân hàng phiếu học tập `data/worksheet_bank.json` (Bài 16–29, 3 phiếu/bài).

Mỗi phiếu phải:
1. `solution_code` qua SyllabusGuard strict cho ĐÚNG bài của nó: 0 lỗi, 0 cảnh báo.
2. `solution_code` qua toàn bộ `test_cases` trong Code Sandbox.
3. Có đủ test (≥3; Bài 16–17 không có input thì ≥2), ít nhất 1 test biên.
Mỗi bài có đúng 3 mức Cơ bản / Đạt chuẩn / Nâng cao; phiếu Cơ bản có chỗ trống `___`.
"""

import ast
import json
from pathlib import Path

import pytest

from src.schemas import WorksheetItem
from src.tools.ast_guard import SyllabusGuard, worksheet_label
from src.tools.code_sandbox import verify_worksheet

ROOT = Path(__file__).resolve().parents[1]
BANK_PATH = ROOT / "data" / "worksheet_bank.json"
RULES_PATH = ROOT / "data" / "syllabus_rules.json"

TIERS = ["Cơ bản", "Đạt chuẩn", "Nâng cao"]
BLOOM = {"Cơ bản": "Nhớ/Hiểu", "Đạt chuẩn": "Vận dụng", "Nâng cao": "Phân tích/Sáng tạo"}
LESSONS = list(range(16, 30))

BANK = json.loads(BANK_PATH.read_text(encoding="utf-8"))
RULES = json.loads(RULES_PATH.read_text(encoding="utf-8"))

CASES = [
    pytest.param(int(bai), ws, id=f"bai{bai}-{ws['tier']}")
    for bai, lesson in sorted(BANK["lessons"].items(), key=lambda kv: int(kv[0]))
    for ws in lesson["worksheets"]
]


@pytest.fixture(scope="module")
def guard():
    return SyllabusGuard.from_file(strict=True)


# ---------------------------------------------------------------- cấu trúc ngân hàng


def test_meta():
    assert BANK["meta"]["description"]
    assert "không trích nguyên văn SGK" in BANK["meta"]["note"]


def test_covers_all_lessons_16_to_29():
    assert sorted(int(k) for k in BANK["lessons"]) == LESSONS
    assert len(CASES) == 42


@pytest.mark.parametrize("bai", LESSONS)
def test_lesson_has_three_tiers_and_title(bai):
    lesson = BANK["lessons"][str(bai)]
    titles = {l["bai"]: l["title"] for l in RULES["lessons"]}
    assert lesson["title"] == titles[bai]
    assert [ws["tier"] for ws in lesson["worksheets"]] == TIERS


# ---------------------------------------------------------------- từng phiếu


@pytest.mark.parametrize("bai,ws", CASES)
def test_schema_and_pedagogy(bai, ws):
    item = WorksheetItem.model_validate(ws)
    assert item.bloom_level == BLOOM[item.tier]
    assert item.prompt_text.strip()
    assert item.solution_code.strip()
    if item.tier == "Cơ bản":
        assert "___" in item.starter_code, "Phiếu Cơ bản phải có chỗ trống ___"
        assert item.starter_code != item.solution_code
    else:
        assert item.starter_code.strip()
        ast.parse(item.starter_code)  # khung/code lỗi lôgic của Đạt chuẩn, Nâng cao vẫn phải đúng cú pháp
    assert "___" not in item.solution_code


@pytest.mark.parametrize("bai,ws", CASES)
def test_test_cases_count_and_edge(bai, ws):
    tests = ws["test_cases"]
    min_cases = 2 if bai in (16, 17) else 3
    assert len(tests) >= min_cases
    assert any(tc["is_edge_case"] for tc in tests), "cần ít nhất 1 test biên"
    if bai >= 18:
        assert any(tc["stdin_data"] for tc in tests), "từ Bài 18 nên có dữ liệu vào"


@pytest.mark.parametrize("bai,ws", CASES)
def test_solution_within_syllabus_scope(guard, bai, ws):
    res = guard.check_code(ws["solution_code"], bai, worksheet_label(ws["tier"]))
    assert res.errors == [], res.messages()
    assert res.warnings == [], [w.message for w in res.warnings]
    assert res.violations == []


@pytest.mark.parametrize("bai,ws", CASES)
def test_solution_passes_sandbox(bai, ws):
    res = verify_worksheet(WorksheetItem.model_validate(ws))
    assert res.passed, res.failure_messages()
    assert not res.warnings, res.warnings
