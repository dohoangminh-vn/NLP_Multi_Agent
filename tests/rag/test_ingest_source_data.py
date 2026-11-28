"""Check source anchors and the textbook text layer on the actual corpus."""

import ast
import hashlib
import json
import re
from pathlib import Path

import pytest
from pypdf import PdfReader
from src.rag.config import RAGConfig
from src.rag.corpus_audit import CorpusManifest
from src.rag.exceptions import ParsingError
from src.rag.parsers.reviewed_workbook import ReviewedWorkbook
from src.rag.parsers.textbook_layout import textbook_markdown
from src.rag.taxonomy import TaxonomyRepository

DATA = Path(__file__).resolve().parents[2] / "data"
RAW = DATA / "raw_pdfs"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_ct2018_links_resolve_to_the_grade_10_table_and_lesson_taxonomy():
    mapping = read_json(DATA / "ct2018_lesson_mapping.json")
    source = mapping["source"]
    manifest = read_json(RAW / "manifest.json")
    spec = next(doc for doc in manifest["documents"] if doc["doc_id"] == source["doc_id"])
    path = RAW / spec["filename"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]
    pdf_page = source["pdf_page"]
    assert spec["page_range"]["start_page"] <= pdf_page <= spec["page_range"]["end_page"]
    text = PdfReader(path).pages[pdf_page - 1].extract_text()
    table = text.split("Chủ đề F.", 1)[1].split("Chủ đề G.", 1)[0]
    # This PDF positions some adjacent letters as separate text runs.
    joined = "".join(table.split())
    for anchor in mapping["anchors"].values():
        assert "".join(anchor["quote"].split()) in joined, anchor
    taxonomy = read_json(DATA / "yccd_taxonomy.json")["lessons"]
    expected = {row["bai_so"]: {y["yccd_id"] for y in row["yccd_items"]} for row in taxonomy}
    assert [lesson["bai_so"] for lesson in mapping["lessons"]] == list(range(16, 30))
    for lesson in mapping["lessons"]:
        links = lesson["links"]
        assert {link["yccd_id"] for link in links} == expected[lesson["bai_so"]]
        assert all(link["anchor"] in mapping["anchors"] for link in links)


def test_unicode_textbook_lesson_boundaries_and_figure_16_1():
    manifest = read_json(RAW / "manifest.json")
    spec = next(doc for doc in manifest["documents"] if doc["doc_type"] == "sgk")
    pages = PdfReader(RAW / spec["filename"]).pages
    lessons = read_json(DATA / "yccd_taxonomy.json")["lessons"]
    assert spec["page_range"] == {
        "start_page": lessons[0]["sgk_page_start"],
        "end_page": lessons[-1]["sgk_page_end"],
    }
    for lesson in lessons:
        first = pages[lesson["sgk_page_start"] - 1].extract_text()
        assert re.search(rf"BÀI\s+{lesson['bai_so']}\b", first)
        assert "/uni" not in first and "%LӃW" not in first
    page = pages[lessons[0]["sgk_page_start"] - 1].extract_text()
    assert "NGÔN NGỮ LẬP TRÌNH BẬC CAO VÀ PYTHON" in " ".join(page.split())
    assert page.rstrip().endswith("\n86")
    reference = (DATA / "review" / "sgk_b16_hinh_16_1.py").read_text(encoding="utf-8")
    lines = page[page.index(reference.splitlines()[0]):].splitlines()[:7]
    assert [line.strip() for line in lines] == [line.strip() for line in reference.splitlines()]
    assert ast.dump(ast.parse("\n".join(lines))) == ast.dump(ast.parse(reference))


def test_native_layout_keeps_code_order_indentation_and_callouts():
    book = PdfReader(next(RAW.glob("*-unicode.pdf")))
    def extract(page):
        return textbook_markdown(book.pages[page - 1], page, "", None)

    reference = (DATA / "review" / "sgk_b16_hinh_16_1.py").read_text(encoding="utf-8").strip()
    assert f"```python\n{reference}\n```" in extract(87)
    assert ">>> 5\n5\n>>> 2.6\n2.6" in extract(89)
    assert "    if k%2 == 0:\n        S = S + k\nprint(S)\n```" in extract(107)
    assert "while k*k < 100:\n    S = S + k*k\n    k = k + 1" in extract(110)
    columns = extract(145).split("print(A[k])", 1)[1]
    assert columns.index("```pycon") < columns.index('>>> s1, s2 = "101010",1010101')


def test_reviewed_workbook_pairs_all_items_and_binds_source(tmp_path):
    config = RAGConfig()
    spec = next(doc for doc in CorpusManifest.model_validate_json(config.manifest_file.read_bytes()).documents
                if doc.doc_type == "sbt")
    source = ReviewedWorkbook(config.workbook_review_dir, TaxonomyRepository.from_json_file(config.taxonomy_file),
                              output_dir=config.processed_dir)
    pages, chunks = source.read(RAW / spec.filename, spec)
    assert [page.page_number for page in pages] == [*range(35, 63), *range(102, 132)]
    items = {(chunk.metadata.muc, chunk.metadata.page_number): chunk for chunk in chunks}
    questions = {section.split()[-1] for section, _ in items if section.startswith("Bài tập")}
    keys = {section.split()[-1] for section, _ in items if section.startswith("Đáp án")}
    assert len(questions) == 162 and questions == keys
    assert ">>> ((z - x*y)*(z - 2*y))**0.5" in items["Đáp án 17.6", 103].content
    assert "        if A[k] >= x:\n            B.append(A[k])" in items["Đáp án 28.11", 129].content
    assert items["Đáp án 29.11", 131].to_source_citation().page_number == 131
    for chunk in chunks:
        for figure in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", chunk.content):
            assert (config.processed_dir / figure).is_file()
    wrong_book = tmp_path / "wrong.pdf"
    wrong_book.write_bytes(b"another edition")
    with pytest.raises(ParsingError, match="edition"):
        source.read(wrong_book, spec)
