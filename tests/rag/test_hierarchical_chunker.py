"""Unit and integration tests for HierarchicalCurriculumChunker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from src.rag.chunking.hierarchical_chunker import HierarchicalCurriculumChunker
from src.rag.exceptions import ChunkingError
from src.rag.interfaces import DocumentChunker
from src.rag.models import DocumentChunk, ParsedPage
from src.rag.parsers.docling_parser import DoclingDocumentParser
from src.rag.taxonomy import TaxonomyRepository

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def sample_taxonomy() -> TaxonomyRepository:
    taxonomy_file = PROJECT_ROOT / "data" / "yccd_taxonomy.json"
    assert taxonomy_file.is_file()
    return TaxonomyRepository.from_json_file(taxonomy_file)


@pytest.fixture
def chunker(sample_taxonomy: TaxonomyRepository) -> HierarchicalCurriculumChunker:
    return HierarchicalCurriculumChunker(taxonomy_provider=sample_taxonomy, max_chunk_size=300)


def test_chunker_conforms_to_document_chunker_protocol(chunker: HierarchicalCurriculumChunker) -> None:
    assert isinstance(chunker, DocumentChunker)


def test_chunk_pages_empty_returns_empty_list(chunker: HierarchicalCurriculumChunker) -> None:
    assert chunker.chunk_pages([]) == []


def test_preserves_fenced_code_blocks(chunker: HierarchicalCurriculumChunker) -> None:
    """Ensure fenced Python code blocks are never cut across chunks."""
    content = """# BÀI 20: CÂU LỆNH LẶP FOR

## 1. Cú pháp câu lệnh for

Dưới đây là một đoạn mã dài minh họa cú pháp lặp for:

```python
# Đoạn code Python có thụt lề 4 khoảng trắng
total = 0
for i in range(1, 101):
    if i % 2 == 0:
        total += i
    else:
        continue
print("Tổng số chẵn:", total)
```

Kết thúc phần ví dụ.
"""
    page = ParsedPage(
        doc_type="sgk",
        doc_name="SGK Tin hoc 10 KNTT",
        page_number=106,
        markdown_content=content,
        bai_so=20,
        ten_bai="Câu lệnh lặp for",
    )

    chunks = chunker.chunk_pages([page])
    assert len(chunks) >= 1

    # Verify that the code block is completely inside one chunk
    code_chunk = next((c for c in chunks if "```python" in c.content), None)
    assert code_chunk is not None
    assert "for i in range(1, 101):" in code_chunk.content
    assert "print(\"Tổng số chẵn:\", total)" in code_chunk.content
    assert code_chunk.content.count("```") == 2
    assert "    if i % 2 == 0:" in code_chunk.content  # 4-space indent preserved


def test_preserves_markdown_tables(chunker: HierarchicalCurriculumChunker) -> None:
    """Ensure Markdown tables are never broken in the middle."""
    content = """# BÀI 19: CÂU LỆNH RẼ NHÁNH

## 1. Biểu thức lôgic

| Toán tử | Ý nghĩa | Ví dụ |
|---|---|---|
| == | Bằng nhau | a == b |
| != | Khác nhau | a != b |
| > | Lớn hơn | a > b |
| < | Nhỏ hơn | a < b |

Phần kết luận sau bảng biểu.
"""
    page = ParsedPage(
        doc_type="sgk",
        doc_name="SGK Tin hoc 10 KNTT",
        page_number=100,
        markdown_content=content,
        bai_so=19,
        ten_bai="Câu lệnh rẽ nhánh",
    )

    chunks = chunker.chunk_pages([page])
    table_chunk = next((c for c in chunks if "| Toán tử | Ý nghĩa |" in c.content), None)
    assert table_chunk is not None
    assert "| < | Nhỏ hơn | a < b |" in table_chunk.content


def test_deterministic_chunk_ids_and_metadata(chunker: HierarchicalCurriculumChunker) -> None:
    """Ensure chunk_id follows deterministic pattern and metadata is fully enriched."""
    content = """# BÀI 20: CÂU LỆNH LẶP FOR

## 1. Cú pháp câu lệnh for
Lý thuyết về vòng lặp for trong Python.

## 2. Luyện tập
Bài tập 1: Viết chương trình in 10 số tự nhiên đầu tiên.
"""
    page = ParsedPage(
        doc_type="sgk",
        doc_name="SGK Tin hoc 10 KNTT",
        page_number=106,
        markdown_content=content,
        bai_so=20,
    )

    chunks = chunker.chunk_pages([page])
    assert len(chunks) >= 2

    for i, c in enumerate(chunks, start=1):
        assert c.chunk_id == f"sgk_b20_p0106_c{i:03d}"
        assert c.metadata.doc_type == "sgk"
        assert c.metadata.bai_so == 20
        assert "câu lệnh lặp for" in c.metadata.ten_bai.lower()
        assert c.metadata.page_number == 106
        assert len(c.metadata.yccd_ids) >= 1
        assert "F10.B20.YC1" in c.metadata.yccd_ids

    # Section 1 is ly_thuyet, Section 2 is bai_tap
    assert chunks[0].metadata.muc == "1. Cú pháp câu lệnh for"
    assert chunks[0].metadata.loai_noi_dung == "ly_thuyet"

    practice_chunk = next(c for c in chunks if "Luyện tập" in c.metadata.muc)
    assert practice_chunk.metadata.loai_noi_dung == "bai_tap"


def test_multi_page_context_inheritance(chunker: HierarchicalCurriculumChunker) -> None:
    """Ensure lesson number and section are inherited across consecutive pages."""
    p1 = ParsedPage(
        doc_type="sgk",
        doc_name="SGK Tin hoc 10 KNTT",
        page_number=106,
        markdown_content="# BÀI 20: CÂU LỆNH LẶP FOR\n\n## 1. Cú pháp\nNội dung cú pháp...",
        bai_so=20,
        ten_bai="Câu lệnh lặp for",
    )
    p2 = ParsedPage(
        doc_type="sgk",
        doc_name="SGK Tin hoc 10 KNTT",
        page_number=107,
        markdown_content="Tiếp tục phần giải thích cú pháp trên trang 107...",
        bai_so=None,  # Not explicitly present on page 2
        ten_bai=None,
    )

    chunks = chunker.chunk_pages([p1, p2])
    assert len(chunks) >= 2

    # Verify page 2 chunks inherited lesson 20 and section title
    p2_chunk = next(c for c in chunks if c.metadata.page_number == 107)
    assert p2_chunk.metadata.bai_so == 20
    assert p2_chunk.metadata.ten_bai == "Câu lệnh lặp for"
    assert p2_chunk.metadata.muc == "1. Cú pháp"


def test_invalid_lesson_number_raises_chunking_error(chunker: HierarchicalCurriculumChunker) -> None:
    page = ParsedPage(
        doc_type="sgk",
        doc_name="SGK Tin hoc 10 KNTT",
        page_number=50,
        markdown_content="# BÀI 10: LƯU TRỮ DỮ LIỆU\n\nNội dung ngoài phạm vi...",
        bai_so=None,
    )
    # Lesson 10 is outside Topic F (16-29)
    with pytest.raises(ChunkingError, match="invalid lesson"):
        chunker.chunk_pages([page])


def test_export_chunks_to_jsonl(tmp_path: Path, chunker: HierarchicalCurriculumChunker) -> None:
    page = ParsedPage(
        doc_type="sgk",
        doc_name="SGK Tin hoc 10 KNTT",
        page_number=106,
        markdown_content="# BÀI 20: CÂU LỆNH LẶP FOR\n\n## 1. Cú pháp\nNội dung...",
        bai_so=20,
        ten_bai="Câu lệnh lặp for",
    )
    chunks = chunker.chunk_pages([page])
    assert len(chunks) >= 1

    out_file = tmp_path / "chunks.jsonl"
    result_path = chunker.export_chunks_to_jsonl(chunks, output_path=out_file)

    assert result_path.is_file()
    lines = result_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == len(chunks)

    parsed_line = json.loads(lines[0])
    assert parsed_line["chunk_id"] == chunks[0].chunk_id
    assert parsed_line["metadata"]["bai_so"] == 20


def test_real_docling_parsed_pages_end_to_end_smoke() -> None:
    """Smoke test: Parse real SGK pages 106-107 and chunk with HierarchicalCurriculumChunker."""
    pdf_path = PROJECT_ROOT / "data" / "raw_pdfs" / "sgk-tin-hoc-10-kntt.pdf"
    assert pdf_path.is_file()

    parser = DoclingDocumentParser(use_docling_native=False)
    pages = parser.parse_document(pdf_path, start_page=106, end_page=107)
    assert len(pages) == 2

    chunker = HierarchicalCurriculumChunker(max_chunk_size=400)
    chunks = chunker.chunk_pages(pages)

    assert len(chunks) > 0
    for chunk in chunks:
        assert isinstance(chunk, DocumentChunk)
        assert chunk.metadata.bai_so == 20
        assert chunk.metadata.page_number in (106, 107)
        assert len(chunk.content) > 0
        assert len(chunk.metadata.yccd_ids) > 0
        # Check source citation conversion works
        citation = chunk.to_source_citation()
        assert citation.bai_so == 20
        assert citation.page_number in (106, 107)
