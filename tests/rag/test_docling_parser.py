"""Unit and integration tests for Docling parser and Markdown post-processor (feat-w1-05)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pypdf import PdfWriter
from src.rag.exceptions import ParsingError
from src.rag.interfaces import DocumentParser
from src.rag.models import ParsedPage
from src.rag.parsers.docling_parser import DoclingDocumentParser
from src.rag.parsers.markdown_postprocessor import MarkdownPostProcessor

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _create_blank_pdf(target: Path, num_pages: int = 5) -> None:
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=300, height=400)
    with target.open("wb") as fh:
        writer.write(fh)


def test_markdown_postprocessor_preserves_code_indentation() -> None:
    processor = MarkdownPostProcessor()
    raw = """
BÀI 20: CÂU LỆNH LẶP FOR

Chương trình tính tổng từ 1 đến 10:

```python
total = 0
for i in range(1, 11):
    if i % 2 == 0:
        total += i
print(“Tổng số chẵn là:”, total)
```

CHỦ ĐỀ F: LẬP TRÌNH PYTHON
KẾT NỐI TRI THỨC VỚI CUỘC SỐNG
106
"""
    res = processor.process(raw)
    assert res.has_code_blocks is True
    assert res.detected_bai_so == 20
    assert "CÂU LỆNH LẶP FOR" in (res.detected_ten_bai or "")

    # Indentation preserved exactly
    assert "    if i % 2 == 0:" in res.cleaned_markdown
    assert "        total += i" in res.cleaned_markdown

    # Smart quote replaced with standard quote
    assert '"Tổng số chẵn là:"' in res.cleaned_markdown

    # Running headers stripped
    assert "KẾT NỐI TRI THỨC VỚI CUỘC SỐNG" not in res.cleaned_markdown


def test_markdown_postprocessor_normalizes_tables() -> None:
    processor = MarkdownPostProcessor()
    raw = """
| STT | Cú pháp | Ý nghĩa |
|---|---|---|
| 1 | range(n) | Dãy từ 0 đến n-1 |
| 2 | range(start, stop) | Dãy từ start đến stop-1 |
"""
    res = processor.process(raw)
    assert res.has_tables is True
    assert "| STT" in res.cleaned_markdown
    assert "| 1 " in res.cleaned_markdown
    assert "| 2 " in res.cleaned_markdown


def test_docling_parser_conforms_to_document_parser_protocol() -> None:
    parser = DoclingDocumentParser(use_docling_native=False)
    assert isinstance(parser, DocumentParser)


def test_docling_parser_file_not_found(tmp_path: Path) -> None:
    parser = DoclingDocumentParser(use_docling_native=False)
    with pytest.raises(FileNotFoundError):
        parser.parse_document(tmp_path / "non_existent.pdf")


def test_docling_parser_invalid_page_bounds(tmp_path: Path) -> None:
    pdf_path = tmp_path / "dummy.pdf"
    _create_blank_pdf(pdf_path, num_pages=5)

    parser = DoclingDocumentParser(use_docling_native=False)
    with pytest.raises(ParsingError, match="start_page"):
        parser.parse_document(pdf_path, start_page=5, end_page=2)


def test_docling_parser_with_mock_docling() -> None:
    mock_doc = MagicMock()
    mock_doc.export_to_markdown.return_value = (
        "# BÀI 20: CÂU LỆNH LẶP FOR\n\n```python\nfor i in range(5):\n    print(i)\n```"
    )

    mock_converter = MagicMock()
    mock_conv_res = MagicMock()
    mock_conv_res.document = mock_doc
    mock_converter.convert.return_value = mock_conv_res

    parser = DoclingDocumentParser(use_docling_native=True)
    parser._docling_converter = mock_converter

    pdf_path = PROJECT_ROOT / "data" / "raw_pdfs" / "sgk-tin-hoc-10-kntt.pdf"
    pages = parser.parse_document(pdf_path, start_page=106, end_page=106)

    assert len(pages) == 1
    page = pages[0]
    assert isinstance(page, ParsedPage)
    assert page.page_number == 106
    assert page.doc_type == "sgk"
    assert page.bai_so == 20
    assert page.has_code_blocks is True


def test_docling_parser_export_pages_to_processed(tmp_path: Path) -> None:
    parser = DoclingDocumentParser(use_docling_native=False)
    pages = [
        ParsedPage(
            doc_type="sgk",
            doc_name="SGK Tin hoc 10 KNTT",
            page_number=106,
            markdown_content="# BÀI 20: CÂU LỆNH LẶP FOR\n\nNội dung bài 20...",
            bai_so=20,
            ten_bai="Câu lệnh lặp For",
            has_tables=False,
            has_code_blocks=True,
        )
    ]

    out_dir = tmp_path / "processed"
    exported = parser.export_pages_to_processed(pages, output_dir=out_dir)

    assert len(exported) == 2
    md_file = out_dir / "sgk_p0106.md"
    json_file = out_dir / "sgk_p0106.json"

    assert md_file.is_file()
    assert json_file.is_file()
    assert "BÀI 20" in md_file.read_text(encoding="utf-8")

    meta = json.loads(json_file.read_text(encoding="utf-8"))
    assert meta["page_number"] == 106
    assert meta["bai_so"] == 20
    assert meta["has_code_blocks"] is True


def test_real_sgk_smoke_parse_lesson_20(tmp_path: Path) -> None:
    """Smoke test running parser on real textbook PDF (Lesson 20: pages 106-107)."""
    pdf_path = PROJECT_ROOT / "data" / "raw_pdfs" / "sgk-tin-hoc-10-kntt.pdf"
    assert pdf_path.is_file()

    parser = DoclingDocumentParser(use_docling_native=False)
    pages = parser.parse_document(pdf_path, start_page=106, end_page=107)

    assert len(pages) == 2
    assert pages[0].page_number == 106
    assert pages[1].page_number == 107
    assert pages[0].doc_type == "sgk"

    # Verify export to output_dir
    exported = parser.export_pages_to_processed(pages, output_dir=tmp_path / "processed")
    assert len(exported) == 4
    for f in exported:
        assert f.is_file()
