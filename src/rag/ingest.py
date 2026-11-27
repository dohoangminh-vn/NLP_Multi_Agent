"""Ingest học liệu (TV2): PDF → Markdown sạch + chunk có metadata phân cấp + taxonomy YCCĐ.

    python -m src.rag.ingest                 # SGK Tin học 10 KNTT, Bài 16–29
    python -m src.rag.ingest --engine docling # dùng Docling (nếu đã cài) thay cho pypdf

Pipeline:
1. Sửa lớp text (font thiếu /ToUnicode) bằng `pdf_unicode_fix` + file map đã commit.
2. Trích text từng trang (pypdf giữ thụt lề code; hoặc Docling).
3. Cắt theo dải trang của từng bài (data/syllabus_rules.json), nhận diện mục ("1. LỆNH FOR",
   "LUYỆN TẬP", "THỰC HÀNH"...), bọc khối code Python vào ```python```.
4. Chunk theo mục (~900 kí tự, chồng lấn 1 đoạn) với metadata:
   doc_type, doc_name, chu_de, bai_so, ten_bai, muc, page_number (TRANG IN), loai_noi_dung.
5. Trích "SAU BÀI NÀY EM SẼ" → data/yccd_taxonomy.json (mã F10.B{bài}.YC{i}).

Đầu ra: data/processed/sgk_chunks.jsonl, data/processed/sgk/bai_XX.md, data/yccd_taxonomy.json
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.rag.pdf_unicode_fix import MAP_DIR, fix_pdf, normalize_text
from src.tools.ast_guard import ROOT, SyllabusRules

DOC_NAME = "SGK Tin học 10 – Kết nối tri thức"
SRC_PDF = ROOT / "sgk-tin-hoc-10-kntt.pdf"
FIXED_PDF = ROOT / "data" / "raw_pdfs" / "sgk_tin10_kntt_unicode.pdf"
OUT_DIR = ROOT / "data" / "processed"
CHUNKS_PATH = OUT_DIR / "sgk_chunks.jsonl"
YCCD_PATH = ROOT / "data" / "yccd_taxonomy.json"
PAGE_OFFSET = 0  # chỉ số trang PDF (0-based) == số trang in của SGK này

_FIXES = {"/dslash": "đ", "/Dslash": "Đ", "\u2012": "-", "\u2212": "-", "\u00a0": " ",
          # ligature của font: "ﬂoat()" → "float()", "ﬁnd()" → "find()"
          "\ufb01": "fi", "\ufb02": "fl", "\ufb00": "ff", "\ufb03": "ffi", "\ufb04": "ffl"}
SECTION_RE = re.compile(r"^(\d+)\.\s+([A-ZÀ-Ỹ0-9ĐƠƯ][A-ZÀ-Ỹ0-9ĐƠƯ ,()'’\-–/]{3,})$")
BLOCK_HEADS = ("LUYỆN TẬP", "VẬN DỤNG", "THỰC HÀNH", "CÂU HỎI", "EM CÓ BIẾT")
_BULLET_RE = re.compile(r"^[x•\-]\s*(?=[A-ZĐÀ-Ỹ])(.*)$")
CODE_RE = re.compile(
    r"^(>>>|\.\.\.|\s{2,}\S|for\s.+:$|while\s.+:$|if\s.+:$|else:$|def\s.+:$|return\b|print\(|[A-Za-z_]\w*\s*=\s*\S|"
    r"[A-Za-z_]\w*\.\w+\(|import\s|global\s|del\s)"
)


@dataclass
class Chunk:
    chunk_id: str
    doc_type: str
    doc_name: str
    chu_de: str
    bai_so: int
    ten_bai: str
    muc: str
    page_number: int
    loai_noi_dung: str
    text: str

    def to_citation(self) -> dict:
        return {"doc_name": self.doc_name, "bai_so": self.bai_so, "section_title": self.muc,
                "page_number": self.page_number, "quoted_text": self.text}


def clean_line(line: str) -> str:
    for a, b in _FIXES.items():
        line = line.replace(a, b)
    return normalize_text(line).rstrip()


def ensure_fixed_pdf(src: Path = SRC_PDF, dst: Path = FIXED_PDF) -> Path:
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        return dst
    maps = json.loads((MAP_DIR / f"{src.stem}.json").read_text(encoding="utf-8"))
    fix_pdf(src, dst, maps)
    return dst


def extract_pages_pypdf(pdf: Path, indices: List[int]) -> Dict[int, List[str]]:
    import pypdf

    reader = pypdf.PdfReader(str(pdf))
    return {i: [clean_line(l) for l in (reader.pages[i].extract_text() or "").splitlines()] for i in indices}


def extract_pages_docling(pdf: Path, indices: List[int]) -> Dict[int, List[str]]:  # pragma: no cover - tuỳ chọn
    from docling.document_converter import DocumentConverter

    doc = DocumentConverter().convert(str(pdf)).document
    pages: Dict[int, List[str]] = {i: [] for i in indices}
    for item, _ in doc.iterate_items():
        prov = getattr(item, "prov", None) or []
        text = getattr(item, "text", "") or ""
        if prov and text:
            idx = prov[0].page_no - 1
            if idx in pages:
                pages[idx].extend(clean_line(l) for l in text.splitlines())
    return pages


def _is_noise(line: str, page_no: int) -> bool:
    s = line.strip()
    return not s or s == str(page_no) or s in ("x", "•")


def classify(muc: str, text: str) -> str:
    u = muc.upper()
    if "SAU BÀI NÀY" in u:
        return "yccd"
    if any(h in u for h in BLOCK_HEADS) or text.lstrip().startswith(("Nhiệm vụ", "1. Viết", "Viết chương trình")):
        return "bai_tap"
    if ">>>" in text or "```python" in text or "Ví dụ" in text:
        return "vi_du"
    return "ly_thuyet"


def fence_code(lines: List[str]) -> List[str]:
    out, buf = [], []

    def flush():
        if len(buf) >= 2 or (buf and buf[0].startswith(">>>")):
            out.append("```python")
            out.extend(buf)
            out.append("```")
        else:
            out.extend(buf)
        buf.clear()

    for l in lines:
        if CODE_RE.match(l) and not re.match(r"^\s*[A-ZĐ][a-zà-ỹ]+\s[a-zà-ỹ]", l):
            buf.append(l)
        else:
            flush()
            out.append(l)
    flush()
    return out


def split_sections(lines_by_page: List[Tuple[int, str]]) -> List[Tuple[str, int, List[str]]]:
    """→ [(tên mục, trang bắt đầu, các dòng)]."""
    sections: List[Tuple[str, int, List[str]]] = []
    cur_title, cur_page, cur = "Mở đầu", lines_by_page[0][0] if lines_by_page else 0, []
    i = 0
    while i < len(lines_by_page):
        page, line = lines_by_page[i]
        s = line.strip()
        head = None
        if SECTION_RE.match(s) or s.startswith(BLOCK_HEADS) or s.startswith("SAU BÀI NÀY EM SẼ"):
            head = s
        if head:
            if cur:
                sections.append((cur_title, cur_page, cur))
            if head.startswith("SAU BÀI NÀY EM SẼ"):
                rest_lines = [ln for _, ln in lines_by_page[i + 1 :]]
                obj, _ = _split_objectives(rest_lines)
                sections.append(("SAU BÀI NÀY EM SẼ", page, obj))
                prev_title = sections[-2][0] if len(sections) >= 2 else "Mở đầu"
                i += 1 + len(obj)
                cur_title, cur_page, cur = prev_title, page, []
                continue
            cur_title, cur_page, cur = head.rstrip(":"), page, []
        else:
            cur.append(line)
        i += 1
    if cur:
        sections.append((cur_title, cur_page, cur))
    return sections


def chunk_section(lines: List[str], max_chars: int = 900) -> List[str]:
    paras, buf = [], []
    for l in fence_code(lines):
        buf.append(l)
        if l.endswith((".", ":", "?", "```")) and sum(len(x) for x in buf) > 250:
            paras.append("\n".join(buf))
            buf = []
    if buf:
        paras.append("\n".join(buf))
    chunks, cur = [], ""
    for p in paras:
        if cur and len(cur) + len(p) > max_chars:
            chunks.append(cur)
            tail = cur.split("\n\n")[-1]
            cur = (tail + "\n\n" if len(tail) < 300 else "") + p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur.strip():
        chunks.append(cur)
    return [c.strip() for c in chunks if len(c.strip()) > 40]


def extract_objectives(lines: List[str]) -> List[str]:
    """Các gạch đầu dòng ngay sau 'SAU BÀI NÀY EM SẼ:'."""
    out: List[str] = []
    started = False
    for l in lines:
        s = l.strip()
        if s.startswith("SAU BÀI NÀY EM SẼ"):
            started = True
            continue
        if not started:
            continue
        m = _BULLET_RE.match(s)
        if m:
            out.append(m.group(1).strip())
        elif out and s and s[0].islower():
            out[-1] += " " + s
        elif out:
            break
    items: List[str] = []
    for o in out:  # gạch đầu dòng 'x' bị dính liền: "... đơn giản. xThực hiện ..."
        items.extend(part.strip() for part in re.split(r"(?<=[.;])\s*x(?=[A-ZĐÀ-Ỹ])", o))
    return [re.sub(r"\s+", " ", o).replace("( )", "()") for o in items if o]


def _split_objectives(lines: List[str]) -> Tuple[List[str], List[str]]:
    """Tách các dòng mục tiêu (gạch đầu dòng) khỏi phần nội dung tiếp theo bị dồn vào cùng mục."""
    obj: List[str] = []
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if _BULLET_RE.match(s) or (obj and s and s[0].islower()):
            obj.append(lines[i])
            i += 1
        else:
            break
    return obj, lines[i:]


def ingest(engine: str = "pypdf", rules: Optional[SyllabusRules] = None) -> dict:
    rules = rules or SyllabusRules.load()
    pdf = ensure_fixed_pdf()
    lessons = rules.lessons
    indices = [p + PAGE_OFFSET for l in lessons for p in range(l["sgk_pages"][0], l["sgk_pages"][1] + 1)]
    pages = (extract_pages_docling if engine == "docling" else extract_pages_pypdf)(pdf, indices)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "sgk").mkdir(exist_ok=True)
    all_chunks: List[Chunk] = []
    taxonomy: Dict[str, dict] = {}
    for l in lessons:
        bai, title = l["bai"], l["title"]
        lines_by_page = [(p, ln) for p in range(l["sgk_pages"][0], l["sgk_pages"][1] + 1)
                         for ln in pages[p + PAGE_OFFSET] if not _is_noise(ln, p)]
        flat = [ln for _, ln in lines_by_page]
        for i, obj in enumerate(extract_objectives(flat), start=1):
            taxonomy[f"F10.B{bai}.YC{i}"] = {"bai_so": bai, "ten_bai": title, "text": obj,
                                             "source": f"{DOC_NAME}, Bài {bai}, mục 'Sau bài này em sẽ', tr.{l['sgk_pages'][0]}"}
        # bỏ dòng tiêu đề bài khỏi nội dung
        body = [(p, ln) for p, ln in lines_by_page if not ln.strip().startswith(f"BÀI {bai}")]
        md = [f"# Bài {bai}. {title}", f"_Nguồn: {DOC_NAME}, tr.{l['sgk_pages'][0]}–{l['sgk_pages'][1]}_", ""]
        n = 0
        for muc, page, sec_lines in split_sections(body):
            md += [f"## {muc} (tr.{page})", ""] + fence_code(sec_lines) + [""]
            for text in chunk_section(sec_lines):
                n += 1
                # trang của chunk = trang chứa dòng đầu tiên của chunk
                first = text.splitlines()[0].strip("`").strip() if text else ""
                cpage = next((p for p, ln in body if p >= page and first and ln.strip() == first), page)
                all_chunks.append(Chunk(f"sgk-b{bai}-{n:02d}", "sgk", DOC_NAME, "F", bai, title, muc, cpage, classify(muc, text), text))
        (OUT_DIR / "sgk" / f"bai_{bai}.md").write_text("\n".join(md), encoding="utf-8")
    with open(CHUNKS_PATH, "w", encoding="utf-8") as f:
        for c in all_chunks:
            f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
    YCCD_PATH.write_text(
        json.dumps({"_meta": {"source": "Trích tự động từ mục 'Sau bài này em sẽ' của SGK (src/rag/ingest.py)",
                              "note": "Cần đối chiếu YCCĐ Thông tư 32/2018 (Chủ đề F lớp 10) khi có PDF Chương trình."},
                    **taxonomy}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return {"chunks": len(all_chunks), "yccd": len(taxonomy), "lessons": len(lessons)}


def load_chunks(path: Path = CHUNKS_PATH) -> List[Chunk]:
    with open(path, encoding="utf-8") as f:
        return [Chunk(**json.loads(l)) for l in f if l.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=["pypdf", "docling"], default="pypdf")
    args = ap.parse_args(argv)
    print(ingest(args.engine))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
