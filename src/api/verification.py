"""Tổng hợp kết quả AST + Sandbox theo từng phiếu để hiển thị trên UI (badge AST PASS / Sandbox PASS)."""

from __future__ import annotations

from typing import List

from src.tools.ast_guard import SyllabusGuard, worksheet_label
from src.tools.code_sandbox import verify_worksheet


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
