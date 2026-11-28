"""Combine the textbook's native reading order with Docling headings and tables."""

import re
import unicodedata
from dataclasses import dataclass
from itertools import groupby

from src.rag.exceptions import ParsingError


@dataclass
class Line:
    text: str
    y: float
    code: bool = False
    note: str = ""


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFC", re.sub(r"ı(?=[\u0300-\u036f])", "i", text)
                                 .replace("ﬁ", "fi").replace("ﬂ", "fl"))


def _native_lines(page) -> list[Line]:
    """The PDF text stream orders columns separately and identifies code by typeface."""
    chars = []

    def collect(text, cm, tm, font, size):
        face = str((font or {}).get("/BaseFont", "")).lower()
        mono = "consolas" in face or "courier" in face
        chars.extend((char, mono, tm[5]) for char in text)

    page.extract_text(visitor_text=collect)
    lines = []
    for newline, group in groupby(chars, key=lambda item: item[0] in "\r\n"):
        if newline:
            continue
        row = list(group)
        full = _normalize("".join(c for c, _, _ in row)).rstrip()
        if not full.strip():
            continue
        mono = _normalize("".join(c for c, code, _ in row if code)).rstrip()
        prose = _normalize("".join(c for c, code, _ in row if not code)).strip()
        y = next(y for c, _, y in row if not c.isspace())
        has_callout = full.lstrip().startswith(mono.lstrip()) and (
            mono.lstrip().startswith(">>>") or mono.endswith(":")
        )
        if mono.strip() and (not prose or has_callout):
            lines.append(Line(mono, y, True, prose))
        else:
            lines.append(Line(full, y))
    return lines


def _table(lines, bounds, layout, number):
    start_text, end_text = bounds
    start = next((i for i, row in enumerate(lines) if row.text.strip().startswith(start_text)), None)
    end = next((i for i, row in enumerate(lines) if start is not None and i >= start
                and row.text.strip().startswith(end_text)), None)
    tables = re.findall(r"(?m)(?:^\|[^\n]+\|[ \t]*\n?)+", layout)
    if start is None or end is None or not tables:
        raise ParsingError(f"Missing textbook table or native boundaries on page {number}")
    if start_text.startswith("Phép toán and"):
        # The three adjacent truth tables share baselines in the native text stream.
        labels = ("and", "or", "not")
        values = [row.text.split() for row in lines[start + 2:end + 1]]
        tables = []
        for label, offset, width in zip(labels, (0, 3, 6), (3, 3, 2), strict=True):
            cells = [row[offset:offset + width] for row in values if len(row) > offset]
            head = ["X", "not X"] if width == 2 else ["X", "Y", f"X {label} Y"]
            table = "\n".join("| " + " | ".join(row) + " |" for row in [head, ["---"] * width, *cells])
            tables.append(f"Phép toán {label}\n\n{table}")
    lines[start:end + 1] = [Line("\n" + "\n\n".join(t.strip() for t in tables) + "\n", lines[start].y)]


def _code_block(rows):
    indents = [0]
    result = []
    for row in rows:
        body = row.text.lstrip()
        indent = len(row.text) - len(body)
        while indents[-1] > indent:
            indents.pop()
        if indent > indents[-1]:
            indents.append(indent)
        result.append("    " * (len(indents) - 1) + body)
    language = "pycon" if any(line.startswith(">>>") for line in result) else "python"
    return "\n```" + language + "\n" + "\n".join(result) + "\n```\n"


def textbook_markdown(page, number: int, layout: str, review: dict | None) -> str:
    review = review or {}
    lines = _native_lines(page)
    if lines and lines[-1].text.strip() == str(number - 1):
        lines.pop()
    table_bounds = review.get("tables", {}).get(str(number))
    if table_bounds:
        _table(lines, table_bounds, layout, number)
    figure = review.get("figures", {}).get(str(number))
    if figure:
        start = next(row for row in lines if row.text == figure["first_line"])
        caption = next(row for row in lines if row.text == figure["caption"])
        caption.y = start.y + 0.5
    headings = {" ".join(h.split()) for h in re.findall(r"(?m)^#+ (.+)$", layout)}
    blocks = []
    # Separate columns before ordering: individual lines from adjacent programs must never interleave.
    flow = 0
    flows = []
    for index, row in enumerate(lines):
        if index and row.y > lines[index - 1].y + 2:
            flow += 1
        flows.append((flow, row))
    for (_, code), group in groupby(flows, key=lambda pair: (pair[0], pair[1].code)):
        rows = [row for _, row in group]
        if code:
            # Machine/assembly listings precede the Python example in Figure 16.1.
            for machine, listing in groupby(rows, key=lambda row: bool(re.match(r"^(?:[01]{8} |[0-9A-F]{4} )", row.text))):
                section = list(listing)
                block = _code_block(section)
                if machine:
                    block = block.replace("```python", "```text", 1)
                notes = [row.note for row in section if row.note]
                blocks.append((section[0].y, "\n".join([block, *notes])))
            continue
        paragraph = []
        for row in rows:
            text = row.text.strip()
            heading = (figure and text == figure["caption"]) or " ".join(text.split()) in headings or re.match(r"^(?:(?:Hoạt động|Ví dụ) \d+\b|BÀI \d+\s)", text) or (
                text.isupper() and len(text) > 4
            )
            starts_block = row.text.startswith("\n") or heading or re.match(r"^(?:[•−–]|\d+[.)] )", text)
            if paragraph and (starts_block or paragraph[-1].y - row.y > 22):
                blocks.append((paragraph[0].y, " ".join(p.text.strip() for p in paragraph)))
                paragraph = []
            if heading:
                blocks.append((row.y, "## " + text))
            elif row.text.startswith("\n"):
                blocks.append((row.y, text))
            else:
                paragraph.append(row)
                if text.endswith((".", "?", "!", ":")):
                    blocks.append((paragraph[0].y, " ".join(p.text.strip() for p in paragraph)))
                    paragraph = []
        if paragraph:
            blocks.append((paragraph[0].y, " ".join(p.text.strip() for p in paragraph)))
    # Text boxes in adjacent columns can differ by hundredths of a point on the same baseline.
    ordered = [text for _, text in sorted(blocks, key=lambda block: -round(block[0], 1))]
    for i in range(1, len(ordered)):
        if ordered[i - 1].startswith("## BÀI ") and ordered[i].startswith("## "):
            title = ordered[i - 1][3:] + " " + ordered[i][3:]
            if title in headings:
                ordered[i - 1], ordered[i] = "", "## " + title
    markdown = "\n\n".join(block for block in ordered if block)
    for original, corrected in review.get("text_corrections", {}).get(str(number), {}).items():
        if original not in markdown:
            raise ParsingError(f"Missing reviewed source text on SGK page {number}: {original!r}")
        markdown = markdown.replace(original, corrected)
    return markdown
