"""Sửa lớp text của PDF SGK: font Type0 (Identity-H) bị thiếu bảng /ToUnicode.

Hiện tượng: pypdf/Docling/PyMuPDF đọc ra chữ rác ("7URQJ" thay vì "Trong") vì PDF chỉ lưu
glyph ID của font Arial/Consolas nhúng mà không kèm bảng glyph → Unicode.

Cách sửa (2 bước):
1. `build-maps`: so khớp HÌNH DẠNG từng glyph nhúng (toạ độ outline đã phẳng hoá) với font tham chiếu
   (Arial, Consolas... bản Microsoft) để suy ra Unicode → lưu `data/font_maps/<pdf>.json`.
   Bước này cần font tham chiếu, chỉ chạy 1 lần; file map được commit để cả nhóm dùng.
2. `fix`: chèn /ToUnicode từ file map vào bản sao PDF → mọi công cụ trích xuất đều đọc đúng tiếng Việt.

    python -m src.rag.pdf_unicode_fix build-maps sgk-tin-hoc-10-kntt.pdf --ref-dir "/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts"
    python -m src.rag.pdf_unicode_fix fix sgk-tin-hoc-10-kntt.pdf data/raw_pdfs/sgk_tin10_kntt_unicode.pdf
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
MAP_DIR = ROOT / "data" / "font_maps"

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


def normalize_text(text: str) -> str:
    """NFC + bỏ ký tự điều khiển – áp dụng sau khi trích text."""
    text = unicodedata.normalize("NFC", text)
    return "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build-maps")
    b.add_argument("pdf")
    b.add_argument("--ref-dir", action="append", required=True)
    b.add_argument("--out", default=None)
    f = sub.add_parser("fix")
    f.add_argument("pdf")
    f.add_argument("out")
    f.add_argument("--maps", default=None)
    args = ap.parse_args(argv)
    pdf = Path(args.pdf)
    default_map = MAP_DIR / f"{pdf.stem}.json"
    if args.cmd == "build-maps":
        maps = build_maps(pdf, [Path(d) for d in args.ref_dir])
        out = Path(args.out) if args.out else default_map
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(maps, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
        print(json.dumps(maps["_stats"], ensure_ascii=False, indent=1))
        print(f"→ {out}")
    else:
        maps = json.loads(Path(args.maps or default_map).read_text(encoding="utf-8"))
        print(fix_pdf(pdf, Path(args.out), maps))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
