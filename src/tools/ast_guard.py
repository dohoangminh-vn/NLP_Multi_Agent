"""AST Syllabus Guard – kiểm định tất định 3 tầng trên `solution_code`.

Tầng 1 – `allowed_ast_nodes`: cấu trúc cú pháp (For, While, ListComp, Lambda, f-string...).
Tầng 2 – `allowed_builtins`: hàm dựng sẵn (print, input, range, len, sum, sorted...).
Tầng 3 – `allowed_methods` + `forbidden_imports`: phương thức (.append, .sort...) và import.

Quy ước (xem `data/syllabus_rules.json`): tập được phép của Bài k là hợp các `unlock`
của mọi bài <= k. Chỉ quét `solution_code` – KHÔNG quét `starter_code` vì phiếu Cơ bản
có chỗ trống `___` sẽ gây SyntaxError giả.

Ví dụ:
    >>> guard = SyllabusGuard.from_file()
    >>> r = guard.check_code("A = [x for x in range(5)]", target_bai=20, label="Phiếu Nâng cao")
    >>> print(r.errors[0].message)
    Phiếu Nâng cao – dòng 1: dùng `ListComp` (list comprehension [... for ...]) vượt phạm vi Bài 20 — chưa có trong SGK Bài 16–29.
"""

from __future__ import annotations

import ast
import builtins
import json
import unicodedata
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Literal, Optional, Sequence, Set

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RULES_PATH = ROOT / "data" / "syllabus_rules.json"

Tier = Literal["syntax", "ast_node", "builtin", "method", "import"]
Severity = Literal["error", "warning"]

_BUILTIN_NAMES: Set[str] = set(dir(builtins))
# Node "ngữ cảnh"/toán tử không có lineno riêng → lấy dòng của node cha.
_PSEUDO_ELIF = "Elif"
_PSEUDO_PERCENT = "PercentFormat"
# Node "con" luôn đi kèm 1 node cha đã bị bắt (ListComp, Try, f-string, def/lambda...)
# → không báo trùng.
_SUBORDINATE = {"comprehension", "ExceptHandler", "FormattedValue", "withitem", "alias", "match_case", "arguments", "arg"}

# ----------------------------------------------------------------------------
# Chuẩn hoá tên phiếu
# ----------------------------------------------------------------------------

_TIER_LABELS = {
    "co ban": "Phiếu Cơ bản",
    "basic": "Phiếu Cơ bản",
    "dat chuan": "Phiếu Đạt chuẩn",
    "standard": "Phiếu Đạt chuẩn",
    "trung binh": "Phiếu Đạt chuẩn",
    "nang cao": "Phiếu Nâng cao",
    "advanced": "Phiếu Nâng cao",
}


def strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.replace("đ", "d").replace("Đ", "D")


def canonical_tier(tier: str) -> Optional[str]:
    """'Nâng cao' / 'advanced' / 'Nhóm Nâng cao' → 'nang cao'. Trả None nếu không nhận ra."""
    t = strip_accents(tier).lower()
    for key, label in _TIER_LABELS.items():
        if key in t:
            return strip_accents(label).lower().replace("phieu ", "")
    return None


def worksheet_label(tier: str, index: Optional[int] = None) -> str:
    t = strip_accents(tier).lower()
    for key, label in _TIER_LABELS.items():
        if key in t:
            return label
    return f"Phiếu {tier}".strip() if tier else f"Phiếu #{(index or 0) + 1}"


# ----------------------------------------------------------------------------
# Kết quả
# ----------------------------------------------------------------------------


@dataclass
class Violation:
    tier: Tier
    name: str  # tên node / builtin / method / module
    line: int
    col: int
    severity: Severity
    message: str
    worksheet: str = ""
    unlocked_at: Optional[int] = None  # bài mở khoá (None = không có trong Bài 16–29)
    hint: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GuardResult:
    worksheet: str
    target_bai: int
    violations: List[Violation] = field(default_factory=list)

    @property
    def errors(self) -> List[Violation]:
        return [v for v in self.violations if v.severity == "error"]

    @property
    def warnings(self) -> List[Violation]:
        return [v for v in self.violations if v.severity == "warning"]

    @property
    def passed(self) -> bool:
        return not self.errors

    def messages(self, include_warnings: bool = False) -> List[str]:
        items = self.violations if include_warnings else self.errors
        return [v.message for v in items]


# ----------------------------------------------------------------------------
# Bộ luật
# ----------------------------------------------------------------------------


@dataclass
class ScopeRules:
    target_bai: int
    ast_nodes: Set[str]
    builtins: Set[str]
    methods: Set[str]
    allowed_imports: Set[str]


class SyllabusRules:
    def __init__(self, data: dict):
        self.data = data
        self.lessons = sorted(data["lessons"], key=lambda l: l["bai"])
        g = data.get("global", {})
        self.always_nodes: Set[str] = set(g.get("always_allowed_nodes", []))
        self.forbidden_imports: Set[str] = set(g.get("forbidden_imports", []))
        self.allowed_imports: Set[str] = set(g.get("allowed_imports", []))
        self.always_forbidden_builtins: Set[str] = set(g.get("always_forbidden_builtins", []))
        self.soft_forbidden: Dict[str, str] = dict(g.get("soft_forbidden", {}))
        self.display_names: Dict[str, str] = dict(data.get("display_names", {}))
        self.hints: Dict[str, object] = dict(data.get("revision_hints", {}))
        self.min_bai = self.lessons[0]["bai"]
        self.max_bai = self.lessons[-1]["bai"]
        # Bài đầu tiên mở khoá mỗi mục
        self.unlock_bai: Dict[str, Dict[str, int]] = {"ast_nodes": {}, "builtins": {}, "methods": {}}
        for lesson in self.lessons:
            for kind in self.unlock_bai:
                for item in lesson.get("unlock", {}).get(kind, []):
                    self.unlock_bai[kind].setdefault(item, lesson["bai"])

    @classmethod
    def load(cls, path: Path | str = DEFAULT_RULES_PATH) -> "SyllabusRules":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    def clamp_bai(self, bai: int) -> int:
        return max(self.min_bai, min(self.max_bai, int(bai)))

    def lesson(self, bai: int) -> Optional[dict]:
        for lesson in self.lessons:
            if lesson["bai"] == bai:
                return lesson
        return None

    def sgk_pages(self, bai: int) -> Optional[Sequence[int]]:
        lesson = self.lesson(bai)
        return lesson.get("sgk_pages") if lesson else None

    def scope(self, target_bai: int) -> ScopeRules:
        bai = self.clamp_bai(target_bai)
        nodes, bis, methods = set(self.always_nodes), set(), set()
        for lesson in self.lessons:
            if lesson["bai"] > bai:
                break
            u = lesson.get("unlock", {})
            nodes.update(u.get("ast_nodes", []))
            bis.update(u.get("builtins", []))
            methods.update(u.get("methods", []))
        return ScopeRules(bai, nodes, bis, methods, set(self.allowed_imports))

    def display(self, name: str) -> str:
        return self.display_names.get(name, "")

    def hint(self, key: str, target_bai: Optional[int] = None) -> str:
        h = self.hints.get(key, "")
        if isinstance(h, dict):
            if target_bai is not None:
                for bai, text in sorted(h.get("before", {}).items(), key=lambda kv: int(kv[0])):
                    if target_bai < int(bai):
                        return text
            return h.get("default", "")
        return h


@lru_cache(maxsize=4)
def _load_cached(path: str) -> SyllabusRules:
    return SyllabusRules.load(path)


# ----------------------------------------------------------------------------
# Guard
# ----------------------------------------------------------------------------


class _Collector(ast.NodeVisitor):
    """Thu thập tên do người dùng tự định nghĩa (biến, hàm, tham số, import)."""

    def __init__(self) -> None:
        self.user_names: Set[str] = set()
        self.import_aliases: Set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.user_names.add(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.user_names.add(node.name)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.user_names.add(node.name)
        self.generic_visit(node)

    def visit_arg(self, node: ast.arg) -> None:
        self.user_names.add(node.arg)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.import_aliases.add((alias.asname or alias.name).split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self.import_aliases.add(alias.asname or alias.name)


class SyllabusGuard:
    def __init__(self, rules: SyllabusRules, strict: bool = False):
        self.rules = rules
        self.strict = strict  # True: các luật 'soft_forbidden' cũng tính là lỗi

    @classmethod
    def from_file(cls, path: Path | str = DEFAULT_RULES_PATH, strict: bool = False) -> "SyllabusGuard":
        return cls(_load_cached(str(path)), strict=strict)

    # ---------------- API chính ----------------

    def check_code(self, code: str, target_bai: int, label: str = "Phiếu") -> GuardResult:
        result = GuardResult(worksheet=label, target_bai=target_bai)
        source = code or ""
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:  # code lời giải phải chạy được → lỗi cứng
            line = exc.lineno or 0
            result.violations.append(
                Violation(
                    tier="syntax",
                    name="SyntaxError",
                    line=line,
                    col=exc.offset or 0,
                    severity="error",
                    worksheet=label,
                    message=f"{label} – dòng {line}: solution_code lỗi cú pháp ({exc.msg}).",
                )
            )
            return result

        scope = self.rules.scope(target_bai)
        collector = _Collector()
        collector.visit(tree)
        lines = source.splitlines()
        seen: Set[tuple] = set()

        def add(v: Violation) -> None:
            key = (v.tier, v.name, v.line)
            if key not in seen:
                seen.add(key)
                result.violations.append(v)

        self._walk(tree, None, scope, collector, lines, label, add)
        result.violations.sort(key=lambda v: (v.line, v.col, v.tier))
        return result

    def check_worksheets(self, worksheets: Iterable, target_bai: int) -> List[GuardResult]:
        """`worksheets`: list[WorksheetItem] hoặc dict có `tier`, `solution_code`."""
        out = []
        for i, ws in enumerate(worksheets):
            tier = _get(ws, "tier", "")
            code = _get(ws, "solution_code", "")
            out.append(self.check_code(code, target_bai, worksheet_label(tier, i)))
        return out

    # ---------------- duyệt cây ----------------

    def _walk(self, node, parent, scope, collector, lines, label, add) -> None:
        line = getattr(node, "lineno", None) or getattr(parent, "_guard_line", 0) or 0
        col = getattr(node, "col_offset", 0) or 0
        node._guard_line = line  # type: ignore[attr-defined]
        name = type(node).__name__

        if isinstance(node, (ast.Import, ast.ImportFrom)):
            self._check_import(node, scope, label, add)
        elif name not in scope.ast_nodes and name not in _SUBORDINATE:
            self._node_violation(name, line, col, scope, label, add)

        # --- luật đặc biệt ---
        if isinstance(node, ast.If) and isinstance(parent, ast.If) and parent.orelse == [node]:
            text = lines[line - 1].lstrip() if 0 < line <= len(lines) else ""
            if text.startswith("elif"):
                self._node_violation(_PSEUDO_ELIF, line, col, scope, label, add)
        if (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Mod)
            and isinstance(node.left, (ast.Constant, ast.JoinedStr))
            and isinstance(getattr(node.left, "value", ""), str)
        ):
            self._node_violation(_PSEUDO_PERCENT, line, col, scope, label, add)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            self._check_builtin(node.id, line, col, scope, collector, label, add)
        if isinstance(node, ast.Attribute):
            base = node.value
            if not (isinstance(base, ast.Name) and base.id in collector.import_aliases):
                self._check_method(node.attr, line, col, scope, label, add)

        for child in ast.iter_child_nodes(node):
            self._walk(child, node, scope, collector, lines, label, add)

    # ---------------- 3 tầng ----------------

    def _scope_phrase(self, kind: str, key: str, target: int) -> tuple[Optional[int], str]:
        bai = self.rules.unlock_bai[kind].get(key)
        if bai is None:
            return None, "chưa có trong SGK Bài 16–29"
        return bai, f"chỉ học từ Bài {bai}"

    def _node_violation(self, name, line, col, scope, label, add) -> None:
        soft = name in self.rules.soft_forbidden
        if soft and name in scope.ast_nodes:
            return
        unlocked, phrase = self._scope_phrase("ast_nodes", name, scope.target_bai)
        display = self.rules.display(name)
        shown = f"`{name}`" + (f" ({display})" if display else "")
        if soft:
            phrase = self.rules.soft_forbidden[name].rstrip(".")
            severity: Severity = "error" if self.strict else "warning"
        else:
            severity = "error"
        add(
            Violation(
                tier="ast_node",
                name=name,
                line=line,
                col=col,
                severity=severity,
                worksheet=label,
                unlocked_at=unlocked,
                hint=self.rules.hint(name, scope.target_bai),
                message=f"{label} – dòng {line}: dùng {shown} vượt phạm vi Bài {scope.target_bai} — {phrase}.",
            )
        )

    def _check_builtin(self, ident, line, col, scope, collector, label, add) -> None:
        if ident not in _BUILTIN_NAMES or ident in collector.user_names:
            return
        if ident in ("True", "False", "None"):
            return
        always_bad = ident in self.rules.always_forbidden_builtins
        if ident in scope.builtins and not always_bad:
            return
        if always_bad:
            unlocked, phrase = None, "bị cấm vì lý do an toàn"
        else:
            unlocked, phrase = self._scope_phrase("builtins", ident, scope.target_bai)
        add(
            Violation(
                tier="builtin",
                name=ident,
                line=line,
                col=col,
                severity="error",
                worksheet=label,
                unlocked_at=unlocked,
                hint=self.rules.hint(ident, scope.target_bai),
                message=f"{label} – dòng {line}: dùng hàm `{ident}()` vượt phạm vi Bài {scope.target_bai} — {phrase}.",
            )
        )

    def _check_method(self, attr, line, col, scope, label, add) -> None:
        if attr in scope.methods:
            return
        unlocked, phrase = self._scope_phrase("methods", attr, scope.target_bai)
        add(
            Violation(
                tier="method",
                name=attr,
                line=line,
                col=col,
                severity="error",
                worksheet=label,
                unlocked_at=unlocked,
                hint=self.rules.hint("." + attr, scope.target_bai),
                message=f"{label} – dòng {line}: dùng phương thức `.{attr}()` vượt phạm vi Bài {scope.target_bai} — {phrase}.",
            )
        )

    def _check_import(self, node, scope, label, add) -> None:
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        else:
            modules = [node.module or "." * node.level]
        for mod in modules:
            root = mod.split(".")[0]
            if root in scope.allowed_imports and root not in self.rules.forbidden_imports:
                continue
            if root in self.rules.forbidden_imports:
                phrase = "module bị cấm vì lý do an toàn"
            else:
                phrase = "SGK Bài 16–29 không dùng thư viện ngoài"
            add(
                Violation(
                    tier="import",
                    name=root,
                    line=node.lineno,
                    col=node.col_offset,
                    severity="error",
                    worksheet=label,
                    hint=self.rules.hint("Import", scope.target_bai),
                    message=f"{label} – dòng {node.lineno}: import `{root}` vượt phạm vi Bài {scope.target_bai} — {phrase}.",
                )
            )


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


# ----------------------------------------------------------------------------
# Hàm tiện dụng cho Critic / Benchmark
# ----------------------------------------------------------------------------


def scope_summary(target_bai: int, rules: Optional[SyllabusRules] = None) -> str:
    rules = rules or SyllabusRules.load()
    scope = rules.scope(target_bai)
    structural = sorted(n for n in scope.ast_nodes if n not in rules.always_nodes)
    names = [f"{n}" + (f" ({rules.display(n)})" if rules.display(n) else "") for n in structural]
    lines = [
        f"Phạm vi cú pháp tích luỹ đến Bài {scope.target_bai} (SGK Tin học 10 KNTT):",
        "- Cấu trúc được phép: " + ", ".join(names),
        "- Hàm dựng sẵn được phép: " + ", ".join(sorted(scope.builtins)),
        "- Phương thức được phép: " + (", ".join("." + m + "()" for m in sorted(scope.methods)) or "(chưa có)"),
        "- Mọi cấu trúc/hàm/phương thức khác đều VƯỢT CHƯƠNG TRÌNH; cấm mọi import.",
    ]
    return "\n".join(lines)


def check_worksheets(worksheets: Iterable, target_bai: int, strict: bool = False) -> List[GuardResult]:
    return SyllabusGuard.from_file(strict=strict).check_worksheets(worksheets, target_bai)


def violation_messages(results: Iterable[GuardResult], include_warnings: bool = False) -> List[str]:
    out: List[str] = []
    for r in results:
        out.extend(r.messages(include_warnings=include_warnings))
    return out


if __name__ == "__main__":  # python -m src.tools.ast_guard <file.py> <bai>
    import sys

    path, bai = sys.argv[1], int(sys.argv[2])
    res = SyllabusGuard.from_file().check_code(Path(path).read_text(encoding="utf-8"), bai, label=Path(path).name)
    for v in res.violations:
        print(f"[{v.severity.upper()}][{v.tier}] {v.message}" + (f"  → Gợi ý: {v.hint}" if v.hint else ""))
    print("PASS" if res.passed else "FAIL")
    sys.exit(0 if res.passed else 1)
