"""Unit test AST Syllabus Guard 3 tầng – DoD: bắt 100% lỗi vượt phạm vi trên bộ test này."""

import json
from pathlib import Path

import pytest

from src.schemas import LessonPlan5512
from src.tools.ast_guard import SyllabusGuard, SyllabusRules, canonical_tier, worksheet_label

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def guard():
    return SyllabusGuard.from_file()


# (bai, code, tier, name) – mỗi dòng PHẢI bị bắt đúng tầng + đúng tên
OUT_OF_SCOPE = [
    # ---- Tầng 1: AST nodes ----
    (18, "a = int(input())\nif a > 0:\n    print(a)", "ast_node", "If"),
    (18, "print(1 < 2)", "ast_node", "Compare"),
    (19, "for i in range(3):\n    print(i)", "ast_node", "For"),
    (20, "i = 0\nwhile i < 3:\n    i = i + 1", "ast_node", "While"),
    (20, "for i in range(5):\n    if i == 3:\n        break", "ast_node", "Break"),
    (20, "A = [1, 2]", "ast_node", "List"),
    (20, "B = [x for x in range(5)]", "ast_node", "ListComp"),
    (20, "f = lambda x: x + 1", "ast_node", "Lambda"),
    (20, "def f(x):\n    return x", "ast_node", "FunctionDef"),
    (20, "s = 'abc'\nprint(s[0])", "ast_node", "Subscript"),
    (21, "A = [1, 2, 3]", "ast_node", "List"),
    (22, "A = [1, 2]\nprint(2 in A)", "ast_node", "In"),
    (22, "A = [1, 2]\nprint(3 not in A)", "ast_node", "NotIn"),
    (25, "def f():\n    return 1", "ast_node", "FunctionDef"),
    (25, "def f():\n    return 1", "ast_node", "Return"),
    (27, "x = 0\ndef f():\n    global x\n    x = 1", "ast_node", "Global"),
    (29, "s = 'abcdef'\nprint(s[1:3])", "ast_node", "Slice"),
    (29, "n = 5\nprint(f'{n}')", "ast_node", "JoinedStr"),
    (29, "n = 5\nprint('%d' % n)", "ast_node", "PercentFormat"),
    (29, "a = 1\nb = 2 if a > 0 else 3", "ast_node", "IfExp"),
    (29, "d = {'a': 1}", "ast_node", "Dict"),
    (29, "s = {1, 2}", "ast_node", "Set"),
    (29, "print(sum(x for x in range(3)))", "ast_node", "GeneratorExp"),
    (29, "try:\n    x = 1 // 0\nexcept ZeroDivisionError:\n    x = 0", "ast_node", "Try"),
    (29, "A = [1, 2]\nprint(*A)", "ast_node", "Starred"),
    (29, "a = None\nprint(a is None)", "ast_node", "Is"),
    (29, "assert 1 == 1", "ast_node", "Assert"),
    (29, "class A:\n    x = 1", "ast_node", "ClassDef"),
    (29, "if (n := 5) > 3:\n    print(n)", "ast_node", "NamedExpr"),
    (29, "print(5 & 3)", "ast_node", "BitAnd"),
    (29, "with x:\n    print(1)", "ast_node", "With"),
    # ---- Tầng 2: built-ins ----
    (17, "x = input()", "builtin", "input"),
    (19, "for_ = 3\nprint(range(for_))", "builtin", "range"),
    (21, "s = 'abc'\nprint(len(s))", "builtin", "len"),
    (20, "n = int(input())\nprint(sum(range(n)))", "builtin", "sum"),
    (20, "for i, x in enumerate(range(3)):\n    print(i)", "builtin", "enumerate"),
    (22, "A = [3, 1]\nB = [2, 4]\nfor a, b in zip(A, B):\n    print(a)", "builtin", "zip"),
    (22, "A = [3, 1]\nprint(sorted(A))", "builtin", "sorted"),
    (29, "A = [3, 1]\nprint(max(A))", "builtin", "max"),
    (26, "A = [3, 1]\nprint(min(A))", "builtin", "min"),
    (25, "print(abs(-3))", "builtin", "abs"),
    (18, "print(round(2.5))", "builtin", "round"),
    (25, "A = list(range(3))", "builtin", "list"),
    (29, "A = [1]\nfor x in reversed(A):\n    print(x)", "builtin", "reversed"),
    (29, "print(open('a.txt'))", "builtin", "open"),
    (29, "eval('1+1')", "builtin", "eval"),
    (29, "exec('x=1')", "builtin", "exec"),
    (29, "__import__('os')", "builtin", "__import__"),
    (28, "f = sum\nprint(f([1]))", "builtin", "sum"),
    # ---- Tầng 3: methods ----
    (20, "A = 0\nA.append(1)", "method", "append"),
    (22, "A = [3, 1]\nA.sort()", "method", "sort"),
    (22, "A = [3, 1]\nA.insert(0, 5)", "method", "insert"),
    (22, "A = [3, 1]\nA.remove(3)", "method", "remove"),
    (24, "s = 'a b'\nprint(s.split())", "method", "split"),
    (24, "s = 'abc'\nprint(s.find('b'))", "method", "find"),
    (29, "A = [3, 1]\nA.pop()", "method", "pop"),
    (26, "s = 'abc'\nprint(s.upper())", "method", "upper"),
    (29, "s = ' a '\nprint(s.strip())", "method", "strip"),
    (29, "s = 'aba'\nprint(s.count('a'))", "method", "count"),
    (29, "s = 'aba'\nprint(s.replace('a', 'b'))", "method", "replace"),
    (29, "print('{}'.format(1))", "method", "format"),
    (29, "A = [1]\nA.extend([2])", "method", "extend"),
    # ---- Tầng 3: imports ----
    (29, "import math\nprint(math.sqrt(4))", "import", "math"),
    (29, "import os", "import", "os"),
    (29, "import sys", "import", "sys"),
    (29, "import subprocess", "import", "subprocess"),
    (29, "from socket import socket", "import", "socket"),
    (29, "import random", "import", "random"),
    # ---- Code không parse được ----
    (29, "for i in range(3)\n    print(i)", "syntax", "SyntaxError"),
]


@pytest.mark.parametrize("bai,code,tier,name", OUT_OF_SCOPE)
def test_out_of_scope_is_caught(guard, bai, code, tier, name):
    res = guard.check_code(code, bai, "Phiếu Nâng cao")
    hits = [v for v in res.errors if v.tier == tier and v.name == name]
    assert hits, f"Không bắt được {tier}:{name} ở Bài {bai}. Bắt được: {[(v.tier, v.name) for v in res.violations]}"
    assert not res.passed
    assert hits[0].line >= 1 and hits[0].message.startswith("Phiếu Nâng cao – dòng ")


IN_SCOPE = [
    (16, "print('Xin chào', 3 + 4 * 2, 7 // 2, 7 % 2, 2 ** 3, -5)"),
    (17, "a, b = 1, 2\nc = a + b\nprint(c)"),
    (18, "ten = input()\ntuoi = int(input())\nx = float(input())\nprint(ten, tuoi, x, type(x), str(tuoi))"),
    (19, "a = int(input())\nif a % 2 == 0 and a > 0:\n    print('chẵn dương')\nelse:\n    if not a > 0 or a == 0:\n        print('khác')"),
    (19, "m = int(input())\nif 1 <= m <= 12:\n    print('tháng hợp lệ')"),
    (20, "n = int(input())\nS = 0\nfor i in range(1, n + 1):\n    if i % 2 == 0:\n        S = S + i\nprint(S, end=' ')"),
    (20, "max = 0\nfor i in range(3):\n    if i > max:\n        max = i\nprint(max)"),
    (21, "n = int(input())\nwhile n > 0:\n    if n == 3:\n        break\n    n = n - 1\nprint(n)"),
    (21, "i = 0\nwhile i < 5:\n    i = i + 1\n    if i == 2:\n        continue\n    print(i)"),
    (22, "A = []\nn = int(input())\nfor i in range(n):\n    A.append(int(input()))\ndel A[0]\nB = A + [1]\nprint(len(B), A[0])"),
    (23, "A = [1, 2, 3]\nA.insert(0, 5)\nA.remove(2)\nif 3 in A and 9 not in A:\n    A.clear()\nfor x in A:\n    print(x)"),
    (24, "s = input()\ndem = 0\nfor ch in s:\n    if ch in 'aeiou':\n        dem = dem + 1\nprint(dem, s[0], len(s))"),
    (25, "s = input()\nds = s.split()\nprint(' '.join(ds), s.find('a'))"),
    (26, "def binh_phuong(n):\n    return n * n\nprint(binh_phuong(int(input())))"),
    (26, "def sum(A):\n    S = 0\n    for x in A:\n        S = S + x\n    return S\nprint(sum([1, 2]))"),
    (27, "def f(a, b, c):\n    return a + b + c\nprint(f(1, 2, 3))"),
    (28, "t = 0\ndef tang():\n    global t\n    t = t + 1\ntang()\nprint(t)"),
    (19, "t = float(input())\nprint(round(t))"),
    (21, "k = 65\nwhile k < 70:\n    print(chr(k), end=' ')\n    k = k + 1"),
    (26, "print(abs(-3), ord('a'), list(range(3)), divmod(7, 2))"),
    (27, "s1 = input()\ns2 = input()\nprint(min(len(s1), len(s2)), s1.upper(), s2.lower())"),
    (28, "s = input()\nprint(s.title())"),
    (29, "A = [1, 2]\nprint(sum(A) / len(A))"),
    (29, "a = int(input())\nb = int(input())\nif b != 0:\n    print(a // b)\nelse:\n    print('Lỗi chia cho 0')"),
]


@pytest.mark.parametrize("bai,code", IN_SCOPE)
def test_in_scope_passes(guard, bai, code):
    res = guard.check_code(code, bai, "Phiếu Đạt chuẩn")
    assert res.passed, [v.message for v in res.errors]


def test_cumulative_unlock_order():
    rules = SyllabusRules.load()
    assert [l["bai"] for l in rules.lessons] == list(range(16, 30))
    assert rules.unlock_bai["ast_nodes"]["If"] == 19
    assert rules.unlock_bai["ast_nodes"]["For"] == 20
    assert rules.unlock_bai["ast_nodes"]["While"] == 21
    assert rules.unlock_bai["ast_nodes"]["List"] == 22
    assert rules.unlock_bai["ast_nodes"]["FunctionDef"] == 26
    s20 = rules.scope(20)
    assert {"For", "If", "Compare"} <= s20.ast_nodes and "range" in s20.builtins
    assert not {"While", "ListComp", "FunctionDef", "Lambda"} & s20.ast_nodes
    assert not {"enumerate", "zip", "sum", "sorted"} & s20.builtins
    assert not {"append", "sort"} & s20.methods


def test_plan_example_message(guard):
    """Thông báo theo đúng ví dụ trong kế hoạch: tên phiếu + dòng + Bài."""
    code = "n = int(input())\nA = 0\nfor i in range(n):\n    B = [x for x in enumerate(range(n))]"
    res = guard.check_code(code, 20, "Phiếu Nâng cao")
    msgs = res.messages()
    assert any(m.startswith("Phiếu Nâng cao – dòng 4: dùng `ListComp`") and "Bài 20" in m for m in msgs)
    assert any(m.startswith("Phiếu Nâng cao – dòng 4: dùng hàm `enumerate()`") for m in msgs)


def test_unlock_lesson_reported(guard):
    res = guard.check_code("A = [1]\nA.append(2)", 20, "Phiếu Cơ bản")
    by_name = {v.name: v for v in res.errors}
    assert by_name["List"].unlocked_at == 22 and "chỉ học từ Bài 22" in by_name["List"].message
    assert by_name["append"].unlocked_at == 22
    assert by_name["append"].hint


def test_soft_rules_warning_by_default_error_when_strict():
    code = "x = int(input())\nif x > 0:\n    x += 1\nelif x == 0:\n    pass\nprint(x)"
    soft = SyllabusGuard.from_file().check_code(code, 21, "P")
    assert soft.passed
    assert {v.name for v in soft.warnings} == {"AugAssign", "Elif", "Pass"}
    strict = SyllabusGuard.from_file(strict=True).check_code(code, 21, "P")
    assert {v.name for v in strict.errors} == {"AugAssign", "Elif", "Pass"}


def test_else_if_nested_is_not_elif(guard):
    code = "x = 1\nif x > 0:\n    print(1)\nelse:\n    if x == 0:\n        print(0)"
    assert not guard.check_code(code, 19, "P").violations


def test_only_solution_code_is_scanned(guard):
    """starter_code có '___' (không parse được) KHÔNG được làm Guard báo lỗi."""
    plan = LessonPlan5512.model_validate(json.loads((FIX / "plan_bai20_clean.json").read_text(encoding="utf-8")))
    assert "___" in plan.worksheets[0].starter_code
    results = guard.check_worksheets(plan.worksheets, 20)
    assert [r.worksheet for r in results] == ["Phiếu Cơ bản", "Phiếu Đạt chuẩn", "Phiếu Nâng cao"]
    assert all(r.passed for r in results), [r.messages() for r in results]


def test_violation_fixture(guard):
    plan = LessonPlan5512.model_validate(json.loads((FIX / "plan_bai20_violations.json").read_text(encoding="utf-8")))
    results = guard.check_worksheets(plan.worksheets, 20)
    assert results[0].passed  # lỗi off-by-one là lỗi logic → việc của Sandbox
    names = {v.name for r in results for v in r.errors}
    assert {"JoinedStr", "ListComp", "max"} <= names


def test_dedup_same_line(guard):
    res = guard.check_code("print(sum(sum([1])))", 28, "P")
    assert len([v for v in res.errors if v.name == "sum"]) == 1


def test_tier_helpers():
    assert canonical_tier("Nhóm Nâng cao") == "nang cao"
    assert canonical_tier("basic") == "co ban"
    assert canonical_tier("Đạt chuẩn") == "dat chuan"
    assert worksheet_label("advanced") == "Phiếu Nâng cao"


def test_target_bai_is_clamped(guard):
    assert guard.check_code("def f():\n    return 1", 32, "P").passed
    assert not guard.check_code("print(1 < 2)", 10, "P").passed


def test_hint_depends_on_target_lesson(guard):
    code = "n = int(input())\nA = [i for i in range(n)]"
    h20 = next(v.hint for v in guard.check_code(code, 20, "P").errors if v.name == "ListComp")
    h24 = next(v.hint for v in guard.check_code(code, 24, "P").errors if v.name == "ListComp")
    assert "append" not in h20 and "append" in h24
