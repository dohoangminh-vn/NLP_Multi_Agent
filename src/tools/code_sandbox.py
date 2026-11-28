"""Code Sandbox Verifier – chạy `solution_code` với `test_cases` trong subprocess cô lập.

Cơ chế cô lập:
* Mỗi test case chạy trong 1 tiến trình Python riêng, thư mục tạm riêng, `timeout = 2s`.
* Ép UTF-8 (`PYTHONIOENCODING=utf-8`, `-X utf8`), môi trường tối giản, `-S` (không nạp site-packages).
* Runner chặn import `os`, `sys`, `subprocess`, `socket`... và vô hiệu hoá `open`, `eval`, `exec`,
  `compile`; giới hạn CPU (RLIMIT_CPU) và kích thước stdout (chống vòng lặp in vô hạn).
* `input()` được thay bằng bản KHÔNG in prompt → stdout chỉ còn kết quả, đối chiếu chính xác
  với `expected_stdout`. Nếu code dùng `input("...")` có prompt, Sandbox ghi cảnh báo.

Lưu ý: đây là sandbox cho code do LLM sinh trong bối cảnh giáo dục (chống lỗi/vô tình),
không phải hàng rào chống mã độc có chủ đích. Khi deploy công khai nên chạy trong Docker.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from src.tools.ast_guard import SyllabusGuard, worksheet_label

DEFAULT_TIMEOUT_S = 2.0
MAX_OUTPUT_BYTES = 256 * 1024
ERROR_MARK = "__SANDBOX_ERROR__"
WARN_MARK = "__SANDBOX_WARN__"

BLOCKED_MODULES = (
    "os", "sys", "subprocess", "socket", "shutil", "pathlib", "ctypes", "importlib", "multiprocessing",
    "threading", "_thread", "signal", "pickle", "marshal", "builtins", "io", "_io", "inspect", "gc",
    "runpy", "code", "tempfile", "glob", "fcntl", "resource", "select", "selectors", "asyncio", "http",
    "urllib", "requests", "ftplib", "smtplib", "webbrowser", "pty", "posix", "nt", "_posixsubprocess",
)

# Runner chạy BÊN TRONG tiến trình con. Giữ độc lập, chỉ dùng thư viện chuẩn.
_RUNNER = r'''
import builtins, json, sys, traceback

ERROR_MARK = %(error_mark)r
WARN_MARK = %(warn_mark)r
BLOCKED = set(%(blocked)r)
MAX_OUT = %(max_out)d
CPU_LIMIT = %(cpu_limit)d

try:
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_LIMIT, CPU_LIMIT + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
except Exception:
    pass

_err = sys.__stderr__
_stdin = sys.stdin
_real_stdout = sys.stdout


class OutputLimitExceeded(Exception):
    pass


class _LimitedStdout:
    def __init__(self, inner):
        self.inner = inner
        self.count = 0

    def write(self, s):
        self.count += len(s.encode("utf-8", "replace"))
        if self.count > MAX_OUT:
            raise OutputLimitExceeded("stdout vượt %%d byte" %% MAX_OUT)
        return self.inner.write(s)

    def flush(self):
        self.inner.flush()

    def __getattr__(self, name):
        return getattr(self.inner, name)


sys.stdout = _LimitedStdout(_real_stdout)

_prompts = []
_real_import = builtins.__import__
_real_exec = builtins.exec


def _from_solution():
    # Chỉ chặn lời gọi phát ra từ code học sinh (solution.py); import nội bộ của stdlib vẫn chạy.
    f = sys._getframe(2)
    return f is not None and f.f_code.co_filename == "solution.py"


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = (name or "").split(".")[0]
    if root in BLOCKED and _from_solution():
        raise ImportError("Sandbox: import '%%s' bị chặn" %% root)
    return _real_import(name, globals, locals, fromlist, level)


def _blocked(fname, real):
    def inner(*args, **kwargs):
        if real is None or _from_solution():
            raise PermissionError("Sandbox: %%s() bị chặn" %% fname)
        return real(*args, **kwargs)
    return inner


def _input(prompt=""):
    if prompt != "":
        _prompts.append(str(prompt))
    line = _stdin.readline()
    if line == "":
        raise EOFError("Chương trình đọc input() nhiều hơn dữ liệu test cung cấp")
    return line.rstrip("\r\n")


path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    source = f.read()

try:
    compiled = compile(source, "solution.py", "exec")
except SyntaxError as exc:
    _err.write(ERROR_MARK + json.dumps({"type": "SyntaxError", "msg": str(exc.msg), "line": exc.lineno}) + "\n")
    sys.exit(1)

builtins.__import__ = _guarded_import
builtins.input = _input
for _name in ("open", "eval", "exec", "compile", "breakpoint", "help", "exit", "quit", "globals", "vars"):
    setattr(builtins, _name, _blocked(_name, getattr(builtins, _name, None)))

code_globals = {"__name__": "__main__", "__builtins__": builtins}
_run = _real_exec
status = 0
try:
    _run(compiled, code_globals)
except SystemExit:
    pass
except BaseException as exc:
    line = None
    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == "solution.py":
            line = tb.tb_lineno
        tb = tb.tb_next
    _err.write("".join(traceback.format_exception_only(type(exc), exc)))
    _err.write(ERROR_MARK + json.dumps({"type": type(exc).__name__, "msg": str(exc), "line": line}, ensure_ascii=False) + "\n")
    status = 1
finally:
    try:
        sys.stdout.flush()
    except BaseException:
        pass
    if _prompts:
        _err.write(WARN_MARK + json.dumps({"input_prompts": _prompts[:5]}, ensure_ascii=False) + "\n")
sys.exit(status)
'''


@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    returncode: Optional[int]
    duration_s: float
    timed_out: bool = False
    error_type: Optional[str] = None  # ZeroDivisionError, EOFError, Timeout, ...
    error_msg: str = ""
    error_line: Optional[int] = None
    input_prompts: List[str] = field(default_factory=list)


@dataclass
class TestCaseResult:
    __test__ = False

    index: int
    passed: bool
    is_edge_case: bool
    status: str  # PASS | WRONG_ANSWER | RUNTIME_ERROR | TIMEOUT | SYNTAX_ERROR
    expected: str
    actual: str
    error_type: Optional[str] = None
    error_msg: str = ""
    error_line: Optional[int] = None
    duration_s: float = 0.0


@dataclass
class WorksheetSandboxResult:
    worksheet: str
    tier: str
    cases: List[TestCaseResult] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    no_test_cases: bool = False

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def n_passed(self) -> int:
        return sum(c.passed for c in self.cases)

    @property
    def passed(self) -> bool:
        return not self.no_test_cases and all(c.passed for c in self.cases)

    def failure_messages(self) -> List[str]:
        if self.no_test_cases:
            return [f"{self.worksheet}: thiếu test_cases → không kiểm chứng được solution_code."]
        out = []
        for c in self.cases:
            if c.passed:
                continue
            tag = f"{self.worksheet} – test #{c.index + 1}" + (" (biên)" if c.is_edge_case else "")
            if c.status == "TIMEOUT":
                out.append(f"{tag}: quá thời gian {DEFAULT_TIMEOUT_S:g}s (nghi vòng lặp vô hạn).")
            elif c.status in ("RUNTIME_ERROR", "SYNTAX_ERROR"):
                where = f" tại dòng {c.error_line}" if c.error_line else ""
                out.append(f"{tag}: lỗi {c.error_type}{where}: {c.error_msg}".rstrip(": "))
            else:
                out.append(
                    f"{tag}: sai kết quả. Mong đợi: {_preview(c.expected)!r} | Nhận được: {_preview(c.actual)!r}"
                )
        return out

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(passed=self.passed, total=self.total, n_passed=self.n_passed)
        return d


# ----------------------------------------------------------------------------


def normalize_output(text: str) -> List[str]:
    """Chuẩn hoá: CRLF→LF, `.strip()` từng dòng, bỏ dòng trống ở cuối."""
    lines = [ln.strip() for ln in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def outputs_match(actual: str, expected: str) -> bool:
    return normalize_output(actual) == normalize_output(expected)


def _preview(text: str, limit: int = 80) -> str:
    text = "\n".join(normalize_output(text))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _child_env() -> dict:
    env = {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    if os.name == "nt":  # Windows cần SYSTEMROOT để khởi động Python
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "C:\\Windows")
    return env


def run_code(code: str, stdin_data: str = "", timeout: float = DEFAULT_TIMEOUT_S) -> ExecutionResult:
    """Chạy 1 lần `code` với `stdin_data`. Không bao giờ ném lỗi ra ngoài."""
    with tempfile.TemporaryDirectory(prefix="tin10_sbx_") as tmp:
        tmp_path = Path(tmp)
        runner = tmp_path / "runner.py"
        solution = tmp_path / "solution.py"
        runner.write_text(
            _RUNNER
            % {
                "error_mark": ERROR_MARK,
                "warn_mark": WARN_MARK,
                "blocked": BLOCKED_MODULES,
                "max_out": MAX_OUTPUT_BYTES,
                "cpu_limit": max(1, int(timeout) + 1),
            },
            encoding="utf-8",
        )
        solution.write_text(code or "", encoding="utf-8")
        cmd = [sys.executable, "-S", "-s", "-B", "-X", "utf8", str(runner), str(solution)]
        data = stdin_data or ""
        if data and not data.endswith("\n"):
            data += "\n"
        start = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd,
                input=data.encode("utf-8"),
                capture_output=True,
                timeout=timeout,
                cwd=tmp,
                env=_child_env(),
            )
        except subprocess.TimeoutExpired as exc:
            return ExecutionResult(
                stdout=(exc.stdout or b"").decode("utf-8", "replace"),
                stderr=(exc.stderr or b"").decode("utf-8", "replace"),
                returncode=None,
                duration_s=time.perf_counter() - start,
                timed_out=True,
                error_type="Timeout",
                error_msg=f"Quá {timeout:g}s",
            )
        duration = time.perf_counter() - start

    stdout = proc.stdout.decode("utf-8", "replace")
    stderr_raw = proc.stderr.decode("utf-8", "replace")
    result = ExecutionResult(stdout=stdout, stderr="", returncode=proc.returncode, duration_s=duration)
    kept = []
    for line in stderr_raw.splitlines():
        if line.startswith(ERROR_MARK):
            info = _safe_json(line[len(ERROR_MARK):])
            result.error_type = info.get("type")
            result.error_msg = info.get("msg", "")
            result.error_line = info.get("line")
        elif line.startswith(WARN_MARK):
            result.input_prompts = _safe_json(line[len(WARN_MARK):]).get("input_prompts", [])
        else:
            kept.append(line)
    result.stderr = "\n".join(kept)
    if proc.returncode not in (0, None) and result.error_type is None:
        # bị kill bởi RLIMIT_CPU hoặc crash cấp thấp
        result.error_type = "Timeout" if proc.returncode in (-9, -24, 137, 152) else "ProcessError"
        result.error_msg = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else f"exit {proc.returncode}"
        result.timed_out = result.error_type == "Timeout"
    return result


def _safe_json(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        return {}


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def verify_worksheet(ws, index: int = 0, timeout: float = DEFAULT_TIMEOUT_S) -> WorksheetSandboxResult:
    tier = _get(ws, "tier", "") or ""
    label = worksheet_label(tier, index)
    code = _get(ws, "solution_code", "") or ""
    tests = list(_get(ws, "test_cases", []) or [])
    res = WorksheetSandboxResult(worksheet=label, tier=tier)
    if not tests:
        res.no_test_cases = True
        return res
    prompts_seen: List[str] = []
    for i, tc in enumerate(tests):
        stdin_data = _get(tc, "stdin_data", "") or ""
        expected = _get(tc, "expected_stdout", "") or ""
        edge = bool(_get(tc, "is_edge_case", False))
        ex = run_code(code, stdin_data, timeout=timeout)
        prompts_seen.extend(ex.input_prompts)
        if ex.timed_out:
            status = "TIMEOUT"
        elif ex.error_type == "SyntaxError":
            status = "SYNTAX_ERROR"
        elif ex.error_type:
            status = "RUNTIME_ERROR"
        elif outputs_match(ex.stdout, expected):
            status = "PASS"
        else:
            status = "WRONG_ANSWER"
        res.cases.append(
            TestCaseResult(
                index=i,
                passed=status == "PASS",
                is_edge_case=edge,
                status=status,
                expected=expected,
                actual=ex.stdout,
                error_type=ex.error_type,
                error_msg=ex.error_msg,
                error_line=ex.error_line,
                duration_s=round(ex.duration_s, 4),
            )
        )
    if prompts_seen:
        res.warnings.append(
            f"{label}: solution_code dùng input() có prompt ({prompts_seen[0]!r}); quy ước là input() rỗng "
            "– Sandbox đã bỏ qua prompt khi đối chiếu."
        )
    return res


def verify_worksheets(worksheets: Iterable, timeout: float = DEFAULT_TIMEOUT_S) -> List[WorksheetSandboxResult]:
    return [verify_worksheet(ws, i, timeout=timeout) for i, ws in enumerate(worksheets)]


def failure_messages(results: Iterable[WorksheetSandboxResult]) -> List[str]:
    out: List[str] = []
    for r in results:
        out.extend(r.failure_messages())
    return out


# ----------------------------------------------------------------------------
# Tổng hợp AST + Sandbox theo phiếu (badge AST PASS / Sandbox n/total trên UI)

def verify_plan(plan, target_bai: int) -> List[dict]:
    guard = SyllabusGuard.from_file()
    out = []
    for i, ws in enumerate(plan.worksheets):
        g = guard.check_code(ws.solution_code, target_bai, worksheet_label(ws.tier, i))
        s = verify_worksheet(ws, i)
        out.append(
            {
                "tier": ws.tier,
                "worksheet": g.worksheet,
                "ast_pass": g.passed,
                "ast_messages": g.messages(),
                "ast_warnings": [v.message for v in g.warnings],
                "sandbox_pass": s.passed,
                "n_passed": s.n_passed,
                "total": s.total,
                "cases": [
                    {
                        "index": c.index,
                        "status": c.status,
                        "stdin": ws.test_cases[c.index].stdin_data,
                        "expected": c.expected,
                        "actual": c.actual,
                        "is_edge_case": c.is_edge_case,
                    }
                    for c in s.cases
                ],
            }
        )
    return out


if __name__ == "__main__":  # python -m src.tools.code_sandbox file.py "stdin" "expected"
    code_path, stdin_text = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "")
    r = run_code(Path(code_path).read_text(encoding="utf-8"), stdin_text.replace("\\n", "\n"))
    print(json.dumps(asdict(r), ensure_ascii=False, indent=2))
