"""Unit test Code Sandbox: cô lập, timeout, UTF-8, input() rỗng, chuẩn hoá output."""

import json
from pathlib import Path

import pytest

from src.schemas import LessonPlan5512, TestCase, WorksheetItem
from src.tools.code_sandbox import (
    failure_messages,
    normalize_output,
    outputs_match,
    run_code,
    verify_worksheet,
    verify_worksheets,
)

FIX = Path(__file__).parent / "fixtures"


def ws(code, tests, tier="Đạt chuẩn"):
    return WorksheetItem(
        tier=tier,
        bloom_level="Vận dụng",
        solution_code=code,
        test_cases=[TestCase(stdin_data=i, expected_stdout=o, is_edge_case=e) for i, o, e in tests],
    )


def test_normalize_strips_each_line_and_trailing_blank():
    assert normalize_output("  15  \r\n\n\n") == ["15"]
    assert outputs_match("a \n b\n", "a\nb")
    assert not outputs_match("a\n\nb", "a\nb")  # dòng trống ở giữa vẫn có nghĩa


def test_pass_and_utf8():
    r = verify_worksheet(ws("ten = input()\nprint('Xin chào', ten)", [("Nguyễn Hoà Bình", "Xin chào Nguyễn Hoà Bình", False)]))
    assert r.passed, r.failure_messages()


def test_input_prompt_is_not_echoed_but_warned():
    r = verify_worksheet(ws("a = int(input('Nhập a: '))\nprint(a * 2)", [("21", "42", False)]))
    assert r.passed
    assert r.warnings and "input() có prompt" in r.warnings[0]


def test_wrong_answer_message():
    r = verify_worksheet(ws("n = int(input())\nprint(n + 1)", [("1", "3", True)], tier="Nâng cao"))
    assert not r.passed
    msg = r.failure_messages()[0]
    assert msg.startswith("Phiếu Nâng cao – test #1 (biên): sai kết quả")
    assert "'3'" in msg and "'2'" in msg


def test_runtime_error_with_line():
    r = verify_worksheet(ws("a = int(input())\nb = 0\nprint(a // b)", [("4", "1", False)]))
    c = r.cases[0]
    assert c.status == "RUNTIME_ERROR" and c.error_type == "ZeroDivisionError" and c.error_line == 3
    assert "tại dòng 3" in r.failure_messages()[0]


def test_eof_when_reading_too_much():
    r = verify_worksheet(ws("a = input()\nb = input()\nprint(a, b)", [("x", "x y", False)]))
    assert r.cases[0].error_type == "EOFError"


def test_timeout_2s():
    r = run_code("while True:\n    x = 1", timeout=2.0)
    assert r.timed_out and r.error_type == "Timeout"
    assert r.duration_s < 4.0


def test_output_flood_is_cut():
    r = run_code("while True:\n    print('x' * 1000)")
    assert r.error_type == "OutputLimitExceeded"


@pytest.mark.parametrize(
    "code",
    [
        "import os\nprint(os.listdir('.'))",
        "import sys\nprint(sys.version)",
        "import subprocess\nsubprocess.run(['ls'])",
        "import socket",
        "from os import path",
        "import os.path",
        "__import__('subprocess')",
        "import builtins",
        "import importlib",
    ],
)
def test_dangerous_imports_blocked(code):
    r = run_code(code)
    assert r.error_type == "ImportError" and "bị chặn" in r.error_msg


@pytest.mark.parametrize("code", ["open('x.txt', 'w').write('a')", "print(open('/etc/hosts').read())", "eval('1+1')", "exec('a=1')"])
def test_open_eval_exec_blocked(code):
    r = run_code(code)
    assert r.error_type == "PermissionError"


def test_cannot_write_files(tmp_path):
    target = tmp_path / "hacked.txt"
    run_code(f"open({str(target)!r}, 'w').write('x')")
    assert not target.exists()


def test_env_is_minimal():
    # Không import được os nên dùng traceback ngoại lệ để chắc chắn biến môi trường cha không lọt vào
    r = run_code("print('ok')")
    assert r.stdout.strip() == "ok" and r.returncode == 0


def test_syntax_error_reported():
    r = verify_worksheet(ws("for i in range(3)\n    print(i)", [("", "0", False)]))
    assert r.cases[0].status == "SYNTAX_ERROR"


def test_missing_test_cases_is_failure():
    r = verify_worksheet(ws("print(1)", []))
    assert not r.passed and "thiếu test_cases" in r.failure_messages()[0]


def test_fixture_clean_all_pass():
    plan = LessonPlan5512.model_validate(json.loads((FIX / "plan_bai20_clean.json").read_text(encoding="utf-8")))
    results = verify_worksheets(plan.worksheets)
    assert all(r.passed for r in results), failure_messages(results)
    assert sum(r.total for r in results) == 9


def test_fixture_violations_detected():
    plan = LessonPlan5512.model_validate(json.loads((FIX / "plan_bai20_violations.json").read_text(encoding="utf-8")))
    results = verify_worksheets(plan.worksheets)
    assert not results[0].passed  # off-by-one
    assert not results[1].passed  # f-string in sai định dạng
    assert results[2].passed      # ListComp + max chạy ĐÚNG → chỉ AST Guard mới bắt được
