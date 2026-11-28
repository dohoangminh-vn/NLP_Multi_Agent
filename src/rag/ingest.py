"""Ingest học liệu (TV2): PDF → sửa font → Markdown sạch → chunk + metadata → YCCĐ → index BM25 + Vector.

    python -m src.rag.ingest                    # SGK Tin học 10 KNTT, Bài 16–29 (data/raw_pdfs/)
    python -m src.rag.ingest --engine docling   # dùng Docling (nếu đã cài) thay cho pypdf
    python -m src.rag.ingest build-font-maps --ref-dir "<thư mục font Arial/Consolas>"   # chỉ khi dựng lại bảng font
    python -m src.rag.ingest fix-pdf            # chỉ chèn /ToUnicode → data/raw_pdfs/sgk_tin10_kntt_unicode.pdf

Pipeline:
1. SỬA LỚP TEXT: font Type0 (Identity-H) của PDF thiếu /ToUnicode nên mọi công cụ (kể cả Docling) đọc ra rác.
   So khớp HÌNH DẠNG glyph với font tham chiếu → bảng glyph→Unicode (data/processed/font_maps/, đã commit)
   → chèn /ToUnicode vào bản sao PDF.
2. Trích text từng trang (pypdf giữ thụt lề code; hoặc Docling), tách ligature (ﬂoat → float).
3. Cắt theo dải trang của từng bài (data/syllabus_rules.json), nhận diện mục ("1. LỆNH FOR",
   "LUYỆN TẬP", "THỰC HÀNH"...), bọc khối code Python vào ```python```.
4. Chunk theo mục (~900 kí tự) với metadata: doc_type, doc_name, chu_de, bai_so, ten_bai, muc,
   page_number (TRANG IN), loai_noi_dung.
5. Trích "SAU BÀI NÀY EM SẼ" → data/yccd_taxonomy.json (mã F10.B{bài}.YC{i}).
6. Dựng index: tách từ tiếng Việt (pyvi) cho BM25 + vector (TF-IDF n-gram kí tự hoặc sentence-transformers)
   → data/processed/index/. `src/rag/hybrid_search.py` nạp index này để tìm kiếm.

Đầu ra: data/processed/{sgk_chunks.jsonl, sgk/bai_XX.md, index/}, data/yccd_taxonomy.json
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import unicodedata
import zlib
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

import numpy as np

from src.tools.ast_guard import ROOT, SyllabusRules

DOC_NAME = "SGK Tin học 10 – Kết nối tri thức"
RAW_DIR = ROOT / "data" / "raw_pdfs"
SRC_PDF = RAW_DIR / "sgk-tin-hoc-10-kntt.pdf"
FIXED_PDF = RAW_DIR / "sgk_tin10_kntt_unicode.pdf"  # bản đã chèn ToUnicode (sinh tự động, không commit)
OUT_DIR = ROOT / "data" / "processed"
MAP_DIR = OUT_DIR / "font_maps"
INDEX_DIR = OUT_DIR / "index"
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


# ============================================================================
# 1. SỬA LỚP TEXT PDF (font Type0 Identity-H thiếu /ToUnicode)
# ============================================================================

# Tên font trong PDF → file font tham chiếu (Microsoft core fonts)
REF_FILES = {
    "ArialMT": ["arial.ttf", "Arial.ttf"],
    "Arial-BoldMT": ["arialbd.ttf", "Arial Bold.ttf"],
    "Arial-ItalicMT": ["ariali.ttf", "Arial Italic.ttf"],
    "Arial-BoldItalicMT": ["arialbi.ttf", "Arial Bold Italic.ttf"],
    "Consolas": ["Consola.ttf", "consola.ttf"],
    "Consolas-Bold": ["Consolab.ttf", "consolab.ttf"],
    "TimesNewRomanPS-BoldItalicMT": ["timesbi.ttf", "Times New Roman Bold Italic.ttf"],
}


def _base_name(font_obj) -> str:
    return str(font_obj.get("/BaseFont", "")).lstrip("/").split("+")[-1]


def _glyph_key(glyf, name) -> Optional[str]:
    g = glyf[name]
    if g.numberOfContours == 0:
        return None
    coords, ends, flags = g.getCoordinates(glyf)
    h = hashlib.sha1()
    h.update(repr(list(coords)).encode())
    h.update(repr(list(ends)).encode())
    h.update(bytes(int(f) & 1 for f in flags))
    return h.hexdigest()


def _unicode_rank(cp: int) -> Tuple[int, int]:
    """Ưu tiên Latin/tiếng Việt khi nhiều mã Unicode cùng 1 hình (A Latin vs Α Hy Lạp vs А Kirin)."""
    if cp < 0x80:
        return (0, cp)
    if 0x1EA0 <= cp <= 0x1EF9 or cp in (0x0102, 0x0103, 0x0110, 0x0111, 0x01A0, 0x01A1, 0x01AF, 0x01B0):
        return (1, cp)
    if cp < 0x250:
        return (2, cp)
    if 0x2000 <= cp <= 0x206F or 0x2190 <= cp <= 0x22FF:
        return (3, cp)
    if 0xE000 <= cp <= 0xF8FF:
        return (9, cp)
    return (5, cp)


def _ref_index(ref_path: Path):
    from fontTools.ttLib import TTFont

    ref = TTFont(str(ref_path))
    cmap = ref.getBestCmap()
    by_name: Dict[str, list] = {}
    for cp, name in cmap.items():
        by_name.setdefault(name, []).append(cp)
    glyf = ref["glyf"]
    order = ref.getGlyphOrder()
    by_hash: Dict[str, int] = {}
    by_hash_text: Dict[str, str] = {}
    for name in order:
        cps = by_name.get(name)
        if not cps:
            continue
        cp = min(cps, key=_unicode_rank)
        k = _glyph_key(glyf, name)
        if k is None:
            continue
        if k not in by_hash or _unicode_rank(cp) < _unicode_rank(by_hash[k]):
            by_hash[k] = cp
    # Glyph biến thể không có trong cmap (vd dấu tiếng Việt chồng) → truy ngược qua GSUB
    name_text: Dict[str, str] = {n: chr(min(cps, key=_unicode_rank)) for n, cps in by_name.items()}
    if "GSUB" in ref:
        for _ in range(3):  # lặp để bắt chuỗi thay thế nhiều tầng
            for lookup in ref["GSUB"].table.LookupList.Lookup:
                for st in lookup.SubTable:
                    st = getattr(st, "ExtSubTable", st)
                    mapping = getattr(st, "mapping", None)
                    if mapping:  # single substitution
                        for src, dst in mapping.items():
                            if dst not in name_text and src in name_text:
                                name_text[dst] = name_text[src]
                    ligs = getattr(st, "ligatures", None)
                    if ligs:  # ligature substitution
                        for first, items in ligs.items():
                            for lig in items:
                                comps = [first] + list(lig.Component)
                                if lig.LigGlyph not in name_text and all(c in name_text for c in comps):
                                    name_text[lig.LigGlyph] = "".join(name_text[c] for c in comps)
                    alts = getattr(st, "alternates", None)
                    if alts:
                        for src, dsts in alts.items():
                            for dst in dsts:
                                if dst not in name_text and src in name_text:
                                    name_text[dst] = name_text[src]
        for name in order:
            if name in by_name or name not in name_text:
                continue
            k = _glyph_key(glyf, name)
            if k is not None and k not in by_hash_text:
                by_hash_text[k] = name_text[name]
    by_gid = {gid: min(by_name[n], key=_unicode_rank) for gid, n in enumerate(order) if n in by_name}
    empty_gids = {gid for gid, n in enumerate(order) if n in by_name and glyf[n].numberOfContours == 0}
    for k, cp in by_hash.items():
        by_hash_text[k] = chr(cp)
    return by_hash_text, by_gid, empty_gids


def _find_ref(name: str, ref_dirs: Iterable[Path]) -> Optional[Path]:
    for d in ref_dirs:
        for fn in REF_FILES.get(name, []):
            p = Path(d) / fn
            if p.exists():
                return p
    return None


def _iter_type0_fonts(reader):
    seen = set()
    for page in reader.pages:
        fonts = (page.get("/Resources") or {}).get("/Font") or {}
        for _, ref in fonts.items():
            f = ref.get_object()
            if f.get("/Subtype") != "/Type0" or "/ToUnicode" in f:
                continue
            ident = id(f) if not hasattr(ref, "idnum") else (ref.idnum, ref.generation)
            if ident in seen:
                continue
            seen.add(ident)
            yield f


def build_maps(pdf_path: Path, ref_dirs: Iterable[Path]) -> Dict[str, Dict[str, str]]:
    import pypdf
    from fontTools.ttLib import TTFont

    reader = pypdf.PdfReader(str(pdf_path))
    maps: Dict[str, Dict[str, str]] = {}
    stats = {}
    cache = {}
    for f in _iter_type0_fonts(reader):
        name = _base_name(f)
        ref_path = _find_ref(name, ref_dirs)
        if ref_path is None:
            stats.setdefault(name, {"status": "no_reference_font"})
            continue
        if ref_path not in cache:
            cache[ref_path] = _ref_index(ref_path)
        by_hash, by_gid, empty_gids = cache[ref_path]
        desc = f["/DescendantFonts"][0].get_object()
        ff = desc["/FontDescriptor"].get_object().get("/FontFile2")
        if ff is None:
            continue
        emb = TTFont(io.BytesIO(ff.get_object().get_data()))
        glyf = emb["glyf"]
        m = maps.setdefault(name, {})
        ok = miss = 0
        for gid, gname in enumerate(emb.getGlyphOrder()):
            if gid == 0:
                continue
            k = _glyph_key(glyf, gname)
            if k is None:
                # glyph rỗng (dấu cách...) – chỉ tin khi cùng GID cũng rỗng ở font tham chiếu
                if gid in empty_gids:
                    m[str(gid)] = chr(by_gid[gid])
                continue
            txt = by_hash.get(k)
            if txt is None:
                miss += 1
                continue
            m[str(gid)] = txt
            ok += 1
        stats[name] = {"reference": ref_path.name, "matched": ok, "unmatched_nonempty": miss}
    maps["_stats"] = stats  # type: ignore[assignment]
    return maps


def _tounicode_cmap(mapping: Dict[str, str]) -> bytes:
    lines = [
        "/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def", "/CMapType 2 def",
        "1 begincodespacerange", "<0000> <FFFF>", "endcodespacerange",
    ]
    items = sorted((int(g), u) for g, u in mapping.items())
    for i in range(0, len(items), 100):
        chunk = items[i : i + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        for gid, u in chunk:
            hexu = "".join(f"{ord(c):04X}" if ord(c) <= 0xFFFF else c.encode("utf-16-be").hex().upper() for c in u)
            lines.append(f"<{gid:04X}> <{hexu}>")
        lines.append("endbfchar")
    lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    return "\n".join(lines).encode("ascii")


def fix_pdf(pdf_in: Path, pdf_out: Path, maps: Dict[str, Dict[str, str]]) -> dict:
    import pypdf
    from pypdf.generic import NameObject, StreamObject

    writer = pypdf.PdfWriter(clone_from=str(pdf_in))
    fixed = {}
    for f in _iter_type0_fonts(writer):
        name = _base_name(f)
        m = maps.get(name)
        if not m:
            continue
        stream = StreamObject()
        stream.set_data(_tounicode_cmap(m))
        f[NameObject("/ToUnicode")] = writer._add_object(stream)
        fixed[name] = fixed.get(name, 0) + 1
    pdf_out.parent.mkdir(parents=True, exist_ok=True)
    with open(pdf_out, "wb") as fh:
        writer.write(fh)
    return fixed


# Tên font trong PDF → file font tham chiếu (Microsoft core fonts)


def normalize_text(text: str) -> str:
    """NFC + bỏ ký tự điều khiển – áp dụng sau khi trích text."""
    text = unicodedata.normalize("NFC", text)
    return "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")


# ============================================================================
# 2. TÁCH TỪ TIẾNG VIỆT + BIỂU DIỄN VECTOR (dùng để dựng index BM25 + Vector)
# ============================================================================

_WORD_RE = re.compile(r"[\wÀ-ỹ]+(?:\(\))?", re.UNICODE)
_STOP = set("và là của các có được cho một những trong với để khi thì này đó ra vào từ theo như em hãy sau đây sẽ ta".split())


def _nfc(t: str) -> str:
    return unicodedata.normalize("NFC", t or "").lower()


class VnTokenizer:
    def __init__(self, use_pyvi: Optional[bool] = None):
        self._pyvi = None
        if use_pyvi is not False:
            try:
                from pyvi import ViTokenizer  # type: ignore

                self._pyvi = ViTokenizer
            except Exception:
                if use_pyvi:
                    raise
        self.name = "pyvi" if self._pyvi else "syllable+bigram"

    def __call__(self, text: str) -> List[str]:
        text = _nfc(text).replace("()", " ")
        if self._pyvi is not None:
            toks = self._pyvi.tokenize(text).split()
            return [t for t in toks if _WORD_RE.fullmatch(t) and t not in _STOP]
        syl = [t for t in _WORD_RE.findall(text) if t not in _STOP]
        return syl + [f"{a}_{b}" for a, b in zip(syl, syl[1:])]


class PlainTokenizer:
    """Baseline KHÔNG tách từ (để chứng minh lợi ích VN segmentation trong retrieval_eval)."""

    name = "whitespace"

    def __call__(self, text: str) -> List[str]:
        return [t for t in _WORD_RE.findall(_nfc(text)) if t not in _STOP]


class Embedder(Protocol):
    name: str

    def encode(self, texts: Sequence[str], is_query: bool = False) -> np.ndarray: ...


class CharNgramEmbedder:
    """TF-IDF n-gram kí tự (3–5) băm vào 2^16 chiều, chuẩn hoá L2. Nhẹ, không cần tải mô hình."""

    name = "char-ngram-tfidf"

    def __init__(self, dim: int = 1 << 16, n_range=(3, 5)):
        self.dim, self.n_range = dim, n_range
        self.idf: Optional[np.ndarray] = None

    def _grams(self, text: str):
        t = f" {' '.join(_WORD_RE.findall(_nfc(text)))} "
        for n in range(self.n_range[0], self.n_range[1] + 1):
            for i in range(len(t) - n + 1):
                yield zlib.crc32(t[i : i + n].encode()) % self.dim  # băm ổn định giữa các lần chạy

    def _tf(self, texts):
        m = np.zeros((len(texts), self.dim), dtype=np.float32)
        for r, t in enumerate(texts):
            for g, c in Counter(self._grams(t)).items():
                m[r, g] = 1 + math.log(c)
        return m

    def fit(self, texts: Sequence[str]) -> "CharNgramEmbedder":
        tf = self._tf(texts)
        df = (tf > 0).sum(axis=0)
        self.idf = np.log((1 + len(texts)) / (1 + df)).astype(np.float32) + 1
        return self

    def encode(self, texts, is_query=False):
        m = self._tf(list(texts)) * (self.idf if self.idf is not None else 1)
        return m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)


class SentenceTransformerEmbedder:  # pragma: no cover - cần tải mô hình
    def __init__(self, model_name: str = "intfloat/multilingual-e5-small"):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_name)
        self.name = model_name
        self.e5 = "e5" in model_name

    def encode(self, texts, is_query=False):
        if self.e5:
            texts = [("query: " if is_query else "passage: ") + t for t in texts]
        return np.asarray(self.model.encode(list(texts), normalize_embeddings=True), dtype=np.float32)


# ============================================================================
# 3. PARSE → CHUNK → METADATA → YCCĐ
# ============================================================================


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
    meta = build_index(all_chunks)
    YCCD_PATH.write_text(
        json.dumps({"_meta": {"source": "Trích tự động từ mục 'Sau bài này em sẽ' của SGK (src/rag/ingest.py)",
                              "note": "Cần đối chiếu YCCĐ Thông tư 32/2018 (Chủ đề F lớp 10) khi có PDF Chương trình."},
                    **taxonomy}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return {"chunks": len(all_chunks), "yccd": len(taxonomy), "lessons": len(lessons), "index": meta}


# ----------------------------------------------------------------------------
# YCCĐ – đọc data/yccd_taxonomy.json (dùng cho Retriever, Outline, Designer, Critic)
# ----------------------------------------------------------------------------


def load_yccd(bai: int, path: Optional[Path | str] = None) -> List[dict]:
    """Trả list {code, text} của Bài `bai`. Hỗ trợ 3 dạng file:
    1) {"F10.B20.YC1": {"bai_so": 20, "text": "..."}}
    2) [{"code": "...", "bai_so": 20, "text": "..."}]
    3) {"20": [{"code": "...", "text": "..."}]}  hoặc {"lessons": [...]} với key 'yccd'
    """
    p = Path(path) if path is not None else YCCD_PATH
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    out: List[dict] = []

    def norm(item, code=None):
        if isinstance(item, str):
            return {"code": code or "", "text": item}
        return {"code": item.get("code", code or ""), "text": item.get("text") or item.get("description") or item.get("noi_dung", "")}

    if isinstance(data, dict):
        data = {k: v for k, v in data.items() if not k.startswith("_")}
    if isinstance(data, dict) and "lessons" in data:
        data = data["lessons"]
    if isinstance(data, list):
        for it in data:
            if int(it.get("bai_so", it.get("bai", -1))) == bai:
                if "yccd" in it:
                    out.extend(norm(x) for x in it["yccd"])
                else:
                    out.append(norm(it))
    elif isinstance(data, dict):
        if str(bai) in data and isinstance(data[str(bai)], list):
            out.extend(norm(x) for x in data[str(bai)])
        else:
            for code, it in data.items():
                if isinstance(it, dict) and int(it.get("bai_so", it.get("bai", -1))) == bai:
                    out.append(norm(it, code))
                elif f".B{bai}." in code:
                    out.append(norm(it, code))
    return out


def load_chunks(path: Path = CHUNKS_PATH) -> List[Chunk]:
    with open(path, encoding="utf-8") as f:
        return [Chunk(**json.loads(l)) for l in f if l.strip()]


# ============================================================================
# 4. DỰNG INDEX BM25 (token đã tách từ) + VECTOR
# ============================================================================


def index_text(c: Chunk) -> str:
    return f"{c.ten_bai}. {c.muc}. {c.text}"


def chunks_fingerprint(chunks: Sequence[Chunk]) -> str:
    h = hashlib.sha256()
    for c in chunks:
        h.update(c.chunk_id.encode())
        h.update(c.text.encode())
    return h.hexdigest()[:16]


def build_index(chunks: Sequence[Chunk], tokenizer=None, embedder=None, out_dir: Path = INDEX_DIR) -> dict:
    """Lưu: bm25_tokens.json (token đã tách từ của từng chunk) + vector (idf n-gram hoặc embedding nơ-ron)."""
    tok = tokenizer or VnTokenizer()
    texts = [index_text(c) for c in chunks]
    out_dir.mkdir(parents=True, exist_ok=True)
    fp = chunks_fingerprint(chunks)
    meta = {"fingerprint": fp, "n_chunks": len(chunks), "tokenizer": tok.name}
    (out_dir / "bm25_tokens.json").write_text(
        json.dumps({**meta, "tokens": [tok(t) for t in texts]}, ensure_ascii=False), encoding="utf-8")
    emb = embedder or CharNgramEmbedder().fit(texts)
    meta["embedder"] = emb.name
    if isinstance(emb, CharNgramEmbedder):
        np.save(out_dir / "dense_char_ngram_idf.npy", emb.idf)
    else:
        np.save(out_dir / f"dense_{emb.name.replace('/', '__')}.npy", emb.encode(texts))
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def load_index(chunks: Sequence[Chunk], tokenizer_name: str, out_dir: Path = INDEX_DIR):
    """→ (tokens, CharNgramEmbedder đã nạp idf) nếu index khớp đúng tập chunk + bộ tách từ; ngược lại None."""
    try:
        meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
        data = json.loads((out_dir / "bm25_tokens.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if meta.get("fingerprint") != chunks_fingerprint(chunks) or data.get("tokenizer") != tokenizer_name:
        return None
    emb = None
    idf_path = out_dir / "dense_char_ngram_idf.npy"
    if idf_path.exists():
        emb = CharNgramEmbedder()
        emb.idf = np.load(idf_path)
    return data["tokens"], emb


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ingest SGK: sửa font PDF → chunk + metadata → YCCĐ → index BM25/Vector")
    ap.add_argument("--engine", choices=["pypdf", "docling"], default="pypdf")
    sub = ap.add_subparsers(dest="cmd")
    b = sub.add_parser("build-font-maps", help="Suy bảng glyph→Unicode từ font tham chiếu (chạy 1 lần, cần font MS)")
    b.add_argument("--pdf", default=str(SRC_PDF))
    b.add_argument("--ref-dir", action="append", required=True)
    f = sub.add_parser("fix-pdf", help="Chèn /ToUnicode vào bản sao PDF")
    f.add_argument("--pdf", default=str(SRC_PDF))
    f.add_argument("--out", default=str(FIXED_PDF))
    args = ap.parse_args(argv)
    if args.cmd == "build-font-maps":
        pdf = Path(args.pdf)
        maps = build_maps(pdf, [Path(d) for d in args.ref_dir])
        out = MAP_DIR / f"{pdf.stem}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(maps, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
        print(json.dumps(maps["_stats"], ensure_ascii=False, indent=1))
        print(f"→ {out}")
    elif args.cmd == "fix-pdf":
        pdf = Path(args.pdf)
        print(fix_pdf(pdf, Path(args.out), json.loads((MAP_DIR / f"{pdf.stem}.json").read_text(encoding="utf-8"))))
    else:
        print(ingest(args.engine))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
