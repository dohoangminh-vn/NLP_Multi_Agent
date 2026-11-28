"""Markdown post-processor for layout cleaning, table formatting, and Python code indentation preservation."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessedMarkdownResult:
    """Result of markdown post-processing."""

    cleaned_markdown: str
    has_tables: bool
    has_code_blocks: bool
    detected_bai_so: int | None
    detected_ten_bai: str | None


class MarkdownPostProcessor:
    """Cleans raw parsed markdown from PDF, preserving tables and Python indentation."""

    # Regex to detect lesson headers like "BÀI 19: CÂU LỆNH ĐIỀU KIỆN IF" or "Bài 20. Câu lệnh lặp for"
    LESSON_HEADER_PATTERN = re.compile(
        r"(?:^|\n)#*\s*BÀI\s+(\d+)\s*[:.\-–]\s*([^\n]+)",
        re.IGNORECASE,
    )

    # Regex for typographic/smart quotes to replace in code blocks
    SMART_QUOTES = {
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "«": '"',
        "»": '"',
        "‹": "'",
        "›": "'",
    }

    # Running headers/footers to strip from raw pages
    HEADER_FOOTER_PATTERNS = [
        re.compile(r"^\s*CHỦ ĐỀ F\s*[:.\-–].*$", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^\s*TIN HỌC 10\s*[-–].*$", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^\s*KẾT NỐI TRI THỨC VỚI CUỘC SỐNG\s*$", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^\s*\d+\s*$", re.MULTILINE),  # standalone page numbers
    ]

    def process(self, raw_markdown: str) -> ProcessedMarkdownResult:
        """Process raw markdown string and return normalized result."""
        if not raw_markdown or not raw_markdown.strip():
            return ProcessedMarkdownResult(
                cleaned_markdown="",
                has_tables=False,
                has_code_blocks=False,
                detected_bai_so=None,
                detected_ten_bai=None,
            )

        # Step 1: Detect lesson headers before stripping headers
        detected_bai_so: int | None = None
        detected_ten_bai: str | None = None
        match = self.LESSON_HEADER_PATTERN.search(raw_markdown)
        if match:
            try:
                num = int(match.group(1))
                if 16 <= num <= 29:
                    detected_bai_so = num
                    detected_ten_bai = match.group(2).strip()
            except ValueError:
                pass

        # Step 2: Separate fenced code blocks from prose so we don't alter code indentation
        parts = self._split_code_blocks(raw_markdown)
        has_code_blocks = any(is_code for _, is_code in parts)

        # Step 3: Process each part
        processed_parts: list[str] = []
        has_tables = False

        for content, is_code in parts:
            if is_code:
                processed_code = self._process_code_block(content)
                processed_parts.append(processed_code)
            else:
                processed_prose = self._process_prose(content)
                if self._contains_table(processed_prose):
                    has_tables = True
                    processed_prose = self._normalize_tables(processed_prose)
                processed_parts.append(processed_prose)

        full_markdown = "".join(processed_parts).strip()

        return ProcessedMarkdownResult(
            cleaned_markdown=full_markdown,
            has_tables=has_tables,
            has_code_blocks=has_code_blocks,
            detected_bai_so=detected_bai_so,
            detected_ten_bai=detected_ten_bai,
        )

    def _split_code_blocks(self, text: str) -> list[tuple[str, bool]]:
        """Split text into segments: (content, is_code_block)."""
        pattern = re.compile(r"(```[a-zA-Z0-9_\-]*\n[\s\S]*?```)", re.MULTILINE)
        result: list[tuple[str, bool]] = []
        last_idx = 0

        for m in pattern.finditer(text):
            if m.start() > last_idx:
                result.append((text[last_idx : m.start()], False))
            result.append((m.group(1), True))
            last_idx = m.end()

        if last_idx < len(text):
            result.append((text[last_idx:], False))

        return result

    def _process_code_block(self, code_fence: str) -> str:
        """Normalize code block: standardize smart quotes, preserve indentation."""
        lines = code_fence.split("\n")
        header = lines[0]
        footer = lines[-1]
        body_lines = lines[1:-1]

        # Ensure language tag is python if none specified
        if header.strip() == "```":
            header = "```python"

        cleaned_body: list[str] = []
        for line in body_lines:
            # Replace typographic quotes with ASCII quotes in code
            for smart, ascii_q in self.SMART_QUOTES.items():
                line = line.replace(smart, ascii_q)

            # Convert tabs to 4 spaces
            line = line.replace("\t", "    ")
            cleaned_body.append(line)

        return header + "\n" + "\n".join(cleaned_body) + "\n" + footer

    def _process_prose(self, prose: str) -> str:
        """Clean running headers, repeated footers, and erratic spacing in prose."""
        cleaned = prose
        for pattern in self.HEADER_FOOTER_PATTERNS:
            cleaned = pattern.sub("", cleaned)

        # Normalize 3+ newlines to 2 newlines
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned

    def _contains_table(self, text: str) -> bool:
        """Check if prose segment contains a Markdown table."""
        # Simple heuristic: at least two lines containing pipes and a separator line |---|
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        pipe_lines = [line for line in lines if line.startswith("|") and line.endswith("|")]
        has_separator = any(re.match(r"^\|(\s*:?-+:?\s*\|)+$", line) for line in pipe_lines)
        return len(pipe_lines) >= 2 and has_separator

    def _normalize_tables(self, text: str) -> str:
        """Ensure Markdown tables have aligned columns and valid header separators."""
        lines = text.split("\n")
        result: list[str] = []
        table_buffer: list[str] = []

        def flush_table(buf: list[str]) -> list[str]:
            if not buf:
                return []
            # Parse cells
            rows: list[list[str]] = []
            for line in buf:
                # Strip leading and trailing pipe
                trimmed = line.strip()
                if trimmed.startswith("|") and trimmed.endswith("|"):
                    cells = [c.strip() for c in trimmed[1:-1].split("|")]
                    rows.append(cells)
                else:
                    rows.append([trimmed])

            # Determine max cols
            max_cols = max(len(r) for r in rows) if rows else 0
            if max_cols < 2:
                return buf

            # Pad rows to max_cols
            for r in rows:
                while len(r) < max_cols:
                    r.append("")

            # Format aligned rows
            col_widths = [max(len(r[c]) for r in rows) for c in range(max_cols)]
            col_widths = [max(w, 3) for w in col_widths]  # minimum width 3

            formatted_lines: list[str] = []
            for r in rows:
                # If row is separator (all hyphens/colons)
                is_separator = all(re.match(r"^:?-+:?$", cell) or cell == "" for cell in r)
                if is_separator:
                    sep_cells = [":" + "-" * (w - 2) + ":" if cell.startswith(":") and cell.endswith(":")
                                 else "-" * w for cell, w in zip(r, col_widths, strict=False)]
                    formatted_lines.append("| " + " | ".join(sep_cells) + " |")
                else:
                    padded_cells = [cell.ljust(w) for cell, w in zip(r, col_widths, strict=False)]
                    formatted_lines.append("| " + " | ".join(padded_cells) + " |")

            return formatted_lines

        in_table = False
        for line in lines:
            trimmed = line.strip()
            if trimmed.startswith("|") and trimmed.endswith("|"):
                in_table = True
                table_buffer.append(line)
            else:
                if in_table:
                    result.extend(flush_table(table_buffer))
                    table_buffer = []
                    in_table = False
                result.append(line)

        if in_table:
            result.extend(flush_table(table_buffer))

        return "\n".join(result)
