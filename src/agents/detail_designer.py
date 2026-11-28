"""Detail & Worksheet Designer (TV3) – sinh chi tiết CV 5512 + 3 phiếu + Revision Mode.

* Có LLM: prompt `src/prompts/designer_*.md` + few-shot từ `src/prompts/worksheet_bank.json`.
  Revision Mode: gửi bản nháp trước + `critic_report.revision_instructions`, chỉ sửa phần lỗi.
* Không có LLM (chế độ offline/demo): dựng giáo án tất định từ khung đã duyệt, chunk SGK đã truy hồi
  và ngân hàng phiếu đã kiểm định. KHÔNG dùng chế độ này để báo cáo số liệu benchmark.

Bất biến sau mọi lần sinh: số phút từng hoạt động = khung GV đã duyệt (HITL là quyết định của GV).
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Dict, List, Optional

from src.agents.profiler import ACTIVITIES, build_profile
from src.prompts import load_prompt
from src.schemas import Activity5512, LessonOutline, LessonPlan5512, LessonRequest, SourceCitation, WorksheetItem
from src.tools.ast_guard import ROOT, _get, scope_summary
from src.rag.ingest import load_yccd
from src.utils.llm import LLMClient, extract_json

BANK_PATH = ROOT / "src" / "prompts" / "worksheet_bank.json"  # few-shot đã kiểm định AST + Sandbox

PLAN_SKELETON = {
    "knowledge_goals": "...", "general_competencies": "...", "cs_competencies": "NLa/NLc: ...", "qualities": "...",
    "teaching_materials": "...",
    "activities": [{
        "activity_name": "Khởi động", "duration_minutes": 0, "goal": "...", "content": "...", "expected_product": "...",
        "step1_transfer": "...", "step2_execute": "...", "step3_report": "...", "step4_conclude": "...",
        "citations": [{"doc_name": "...", "bai_so": 0, "section_title": "...", "page_number": 0, "quoted_text": "..."}],
    }],
    "worksheets": [{
        "tier": "Cơ bản | Đạt chuẩn | Nâng cao", "bloom_level": "Nhớ/Hiểu | Vận dụng | Phân tích/Sáng tạo",
        "prompt_text": "...", "starter_code": "...", "solution_code": "...",
        "test_cases": [{"stdin_data": "...", "expected_stdout": "...", "is_edge_case": False}],
    }],
}


@lru_cache(maxsize=1)
def load_bank() -> Dict[str, dict]:
    if not BANK_PATH.exists():
        return {}
    return json.loads(BANK_PATH.read_text(encoding="utf-8")).get("lessons", {})


def bank_worksheets(bai: int) -> List[WorksheetItem]:
    return [WorksheetItem.model_validate(w) for w in load_bank().get(str(bai), {}).get("worksheets", [])]


def _activity_name(title: str) -> str:
    for a in ACTIVITIES:
        if title.strip().lower().startswith(a.lower()):
            return a
    return title.split(":")[0].strip() or title


def enforce_outline(plan: LessonPlan5512, outline: Optional[LessonOutline]) -> LessonPlan5512:
    """Ép số phút (và thứ tự) theo khung đã duyệt khi số hoạt động khớp."""
    if outline and len(outline.activity_minutes) == len(plan.activities):
        for act, m in zip(plan.activities, outline.activity_minutes):
            act.duration_minutes = int(m)
    return plan


def _format_chunks(chunks: List[SourceCitation], limit: int = 9000) -> str:
    out, used = [], 0
    for i, c in enumerate(chunks, start=1):
        s = f"[{i}] doc_name=\"{c.doc_name}\" bai_so={c.bai_so} section_title=\"{c.section_title}\" page_number={c.page_number}\n{c.quoted_text}"
        if used + len(s) > limit:
            break
        out.append(s)
        used += len(s)
    return "\n\n".join(out) or "(không có)"


class DetailDesigner:
    def __init__(self, llm: Optional[LLMClient] = None, yccd_loader=load_yccd, max_json_retries: int = 1):
        self.llm = llm
        self.yccd_loader = yccd_loader
        self.max_json_retries = max_json_retries

    # ---------------- LLM ----------------

    def build_prompt(self, req: LessonRequest, outline: Optional[LessonOutline], profile: dict, chunks: List[SourceCitation]) -> str:
        yccd = self.yccd_loader(req.target_bai)
        bank = bank_worksheets(req.target_bai)
        fewshot = json.dumps([{"tier": w.tier, "bloom_level": w.bloom_level, "prompt_text": w.prompt_text} for w in bank],
                             ensure_ascii=False, indent=1) if bank else "(không có)"
        ol = outline or LessonOutline()
        outline_txt = "\n".join(f"- {t} – {m} phút" for t, m in zip(ol.activity_titles, ol.activity_minutes))
        outline_txt += "\nMục tiêu 3 phiếu: " + "; ".join(ol.worksheet_goals)
        return load_prompt("designer_user").format(
            bai=req.target_bai, topic=req.topic_name, scope_mode=req.scope_mode, minutes=req.target_minutes,
            ratios=f"{req.basic_ratio:.0%}/{req.standard_ratio:.0%}/{req.advanced_ratio:.0%}",
            emphasis=profile.get("emphasis", ""), lab_notes=" ".join(profile.get("lab_notes", [])),
            outline=outline_txt, yccd="\n".join(f"- {y['code']}: {y['text']}" for y in yccd) or "(chưa có)",
            scope=scope_summary(req.target_bai), context=_format_chunks(chunks), fewshot=fewshot,
            skeleton=json.dumps(PLAN_SKELETON, ensure_ascii=False),
        )

    def _ask(self, user: str) -> Optional[LessonPlan5512]:
        note = ""
        for _ in range(1 + self.max_json_retries):
            text = self.llm.complete(load_prompt("designer_system"), user + note).text
            data = extract_json(text)
            if data is not None:
                try:
                    return LessonPlan5512.model_validate(data)
                except Exception as exc:
                    note = f"\n\nLƯU Ý: lần trước JSON sai cấu trúc ({str(exc)[:200]}). Trả lại đúng khung."
                    continue
            note = "\n\nLƯU Ý: lần trước không trả về JSON hợp lệ. Chỉ trả về JSON."
        return None

    def design(self, req: LessonRequest, outline: Optional[LessonOutline], profile: dict, chunks: List[SourceCitation],
               previous: Optional[LessonPlan5512] = None, revision: str = "", iteration: int = 0) -> Optional[LessonPlan5512]:
        if self.llm is None:
            if previous is not None and revision:
                return self.offline_revise(req, previous)
            return self.offline_plan(req, outline, profile, chunks)
        user = self.build_prompt(req, outline, profile, chunks)
        if previous is not None and revision:
            user += "\n\n" + load_prompt("designer_revision").format(previous=previous.model_dump_json(), iteration=iteration, instructions=revision)
        plan = self._ask(user)
        if plan is None:
            return previous  # giữ bản hợp lệ gần nhất
        return enforce_outline(plan, outline)

    # ---------------- offline ----------------

    def offline_plan(self, req: LessonRequest, outline: Optional[LessonOutline], profile: dict, chunks: List[SourceCitation]) -> LessonPlan5512:
        own = [c for c in chunks if c.bai_so == req.target_bai] or list(chunks)
        groups = {
            "Khởi động": [c for c in own if c.section_title.startswith(("SAU BÀI", "Mở đầu", "1."))][:1],
            "Hình thành kiến thức": [c for c in own if c.section_title[:1].isdigit()][:3],
            "Luyện tập": [c for c in own if c.section_title.startswith(("THỰC HÀNH", "LUYỆN TẬP"))][:2],
            "Vận dụng": [c for c in own if c.section_title.startswith("VẬN DỤNG")][:1],
        }
        ol = outline or LessonOutline(activity_titles=ACTIVITIES, activity_minutes=[a["minutes"] for a in profile["activities"]])
        yccd = self.yccd_loader(req.target_bai)
        emphasis = profile.get("emphasis", "")
        acts = []
        for title, minutes in zip(ol.activity_titles, ol.activity_minutes):
            name = _activity_name(title)
            cits = [c.model_copy(update={"quoted_text": c.quoted_text[:240]}) for c in groups.get(name, [])]
            content = "\n\n".join(c.quoted_text[:400] for c in cits) or title
            acts.append(Activity5512(
                activity_name=name, duration_minutes=int(minutes), goal=title.split(":", 1)[-1].strip(),
                content=content,
                expected_product={"Khởi động": "Câu trả lời của HS cho tình huống mở đầu",
                                  "Hình thành kiến thức": "HS nêu được cú pháp, chạy được ví dụ SGK",
                                  "Luyện tập": "Bài làm 3 phiếu học tập theo nhóm trình độ",
                                  "Vận dụng": "Chương trình giải bài toán vận dụng"}.get(name, "Sản phẩm học tập của HS"),
                step1_transfer=f"GV giao nhiệm vụ: {title}. {emphasis if name == 'Hình thành kiến thức' else ''}".strip(),
                step2_execute="HS làm việc cá nhân/cặp đôi trên máy tính; GV quan sát, hỗ trợ nhóm Cơ bản." if name == "Luyện tập"
                else "HS thực hiện nhiệm vụ theo hướng dẫn trong SGK; GV theo dõi, gợi ý.",
                step3_report="Đại diện HS trình bày kết quả, các HS khác nhận xét, bổ sung.",
                step4_conclude="GV nhận xét, chốt kiến thức trọng tâm" + (f": {yccd[0]['text']}" if yccd and name == "Hình thành kiến thức" else "."),
                citations=cits,
            ))
        lesson_goals = " ".join(y["text"] for y in yccd)
        return LessonPlan5512(
            knowledge_goals=lesson_goals or ol.objective_summary,
            general_competencies="Tự chủ và tự học; Giao tiếp và hợp tác; Giải quyết vấn đề và sáng tạo.",
            cs_competencies="NLc: Giải quyết vấn đề với sự hỗ trợ của CNTT và truyền thông; NLa: Sử dụng môi trường lập trình Python.",
            qualities="Chăm chỉ, trách nhiệm, trung thực khi kiểm thử chương trình.",
            teaching_materials=f"Phòng máy có Python; SGK, SBT Tin học 10 (Kết nối tri thức); 3 phiếu học tập. {req.lab_constraints}".strip(),
            activities=acts,
            worksheets=bank_worksheets(req.target_bai),
        )

    def offline_revise(self, req: LessonRequest, previous: LessonPlan5512) -> LessonPlan5512:
        """Offline: thay phiếu lỗi bằng phiếu đã kiểm định trong ngân hàng (cùng tier)."""
        bank = {w.tier: w for w in bank_worksheets(req.target_bai)}
        plan = previous.model_copy(deep=True)
        plan.worksheets = [bank.get(w.tier, w) for w in plan.worksheets] or list(bank.values())
        return plan

    # ---------------- LangGraph node ----------------

    def __call__(self, state) -> dict:
        req = _get(state, "request")
        req = LessonRequest.model_validate(req) if isinstance(req, dict) else req
        outline = _get(state, "outline")
        outline = LessonOutline.model_validate(outline) if isinstance(outline, dict) else outline
        raw = _get(state, "bloom_matrix_json", "") or ""
        profile = json.loads(raw) if raw else build_profile(req)
        chunks = [c if isinstance(c, SourceCitation) else SourceCitation.model_validate(c) for c in (_get(state, "retrieved_chunks", []) or [])]
        prev = _get(state, "draft_plan")
        prev = LessonPlan5512.model_validate(prev) if isinstance(prev, dict) else prev
        report = _get(state, "critic_report")
        revision = (_get(report, "revision_instructions", "") or "") if report is not None and not _get(report, "is_passed", False) else ""
        plan = self.design(req, outline, profile, chunks, prev, revision, int(_get(state, "iteration", 0) or 0))
        return {"draft_plan": plan}
