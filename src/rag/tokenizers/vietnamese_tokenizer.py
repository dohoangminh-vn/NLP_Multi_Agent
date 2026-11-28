"""Vietnamese word segmentation with curriculum terminology preservation."""

from __future__ import annotations

import re

from pyvi import ViTokenizer


class PyViVietnameseTokenizer:
    """Use PyVi for prose and preserve Python code and curriculum terms."""

    _code_span = re.compile(r"(```[\s\S]*?```|`[^`\n]+`)")
    _word = re.compile(r"\w+(?:_\w+)*", re.UNICODE)
    _terms = (
        ("câu", "lệnh"),
        ("rẽ", "nhánh"),
        ("xâu", "kí", "tự"),
        ("danh", "sách"),
        ("chương", "trình", "con"),
    )

    def segment_words(self, text: str) -> str:
        """Segment natural language while retaining fenced and inline code verbatim."""
        parts = self._code_span.split(text)
        for index in range(0, len(parts), 2):
            segmented = ViTokenizer.tokenize(parts[index])
            for term in self._terms:
                pattern = r"(?<!\w)" + r"[ _]+".join(map(re.escape, term)) + r"(?!\w)"
                segmented = re.sub(pattern, "_".join(term), segmented, flags=re.IGNORECASE)
            parts[index] = segmented
        return "".join(parts)

    def tokenize(self, text: str) -> list[str]:
        """Return normalized search terms, including intact Python identifiers."""
        return [match.group().lower() for match in self._word.finditer(self.segment_words(text))]
