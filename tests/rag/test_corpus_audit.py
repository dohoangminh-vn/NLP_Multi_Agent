"""Unit and integration tests for raw PDF corpus manifest and auditor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pypdf import PdfWriter
from src.rag.corpus_audit import CorpusAuditor, CorpusManifest, PageRange

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _create_blank_pdf(target: Path, num_pages: int) -> None:
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=200, height=200)
    with target.open("wb") as fh:
        writer.write(fh)


def test_page_range_rejects_inverted_bounds() -> None:
    with pytest.raises(ValueError, match="end_page"):
        PageRange(start_page=50, end_page=10)


def test_auditor_happy_path_with_temp_pdfs(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw_pdfs"
    raw_dir.mkdir()
    _create_blank_pdf(raw_dir / "sgk.pdf", num_pages=20)
    _create_blank_pdf(raw_dir / "sbt.pdf", num_pages=15)

    manifest_path = raw_dir / "manifest.json"
    manifest_data = {
        "version": "1.0",
        "scope": "Chu de F - Bai 16-29",
        "documents": [
            {
                "doc_id": "sgk_10_kntt",
                "doc_type": "sgk",
                "doc_name": "SGK Tin hoc 10 KNTT",
                "filename": "sgk.pdf",
                "page_range": {"start_page": 5, "end_page": 18},
            },
            {
                "doc_id": "sbt_10_kntt",
                "doc_type": "sbt",
                "doc_name": "SBT Tin hoc 10 KNTT",
                "filename": "sbt.pdf",
                "page_range": {"start_page": 1, "end_page": 15},
            },
        ],
    }
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    auditor = CorpusAuditor(raw_dir=raw_dir)
    report = auditor.audit(manifest_path)

    assert report.all_passed is True
    assert report.passed_documents == 2
    assert all(res.sha256 is not None and len(res.sha256) == 64 for res in report.results)


def test_auditor_reports_missing_and_corrupted_and_out_of_range(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw_pdfs"
    raw_dir.mkdir()
    (raw_dir / "corrupt.pdf").write_bytes(b"not a valid pdf content")
    _create_blank_pdf(raw_dir / "short.pdf", num_pages=5)

    manifest_path = raw_dir / "manifest.json"
    manifest_data = {
        "version": "1.0",
        "scope": "Test error paths",
        "documents": [
            {
                "doc_id": "missing_doc",
                "doc_type": "ct2018",
                "doc_name": "Missing CT2018",
                "filename": "does_not_exist.pdf",
                "page_range": {"start_page": 1, "end_page": 10},
            },
            {
                "doc_id": "corrupt_doc",
                "doc_type": "sgk",
                "doc_name": "Corrupt SGK",
                "filename": "corrupt.pdf",
                "page_range": {"start_page": 1, "end_page": 2},
            },
            {
                "doc_id": "short_doc",
                "doc_type": "sbt",
                "doc_name": "Short SBT",
                "filename": "short.pdf",
                "page_range": {"start_page": 1, "end_page": 10},
            },
        ],
    }
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    auditor = CorpusAuditor(raw_dir=raw_dir)
    report = auditor.audit(manifest_path)

    assert report.all_passed is False
    assert report.passed_documents == 0
    assert report.results[0].exists is False
    assert report.results[0].error is not None
    assert report.results[1].readable is False
    assert report.results[1].error is not None
    assert report.results[2].readable is True
    assert report.results[2].range_valid is False
    assert report.results[2].error is not None


def test_real_corpus_manifest_passes() -> None:
    raw_dir = PROJECT_ROOT / "data" / "raw_pdfs"
    manifest_path = raw_dir / "manifest.json"
    assert manifest_path.is_file(), "data/raw_pdfs/manifest.json must exist"

    manifest = CorpusManifest.model_validate(
        json.loads(manifest_path.read_text(encoding="utf-8"))
    )
    doc_types = {doc.doc_type for doc in manifest.documents}
    assert doc_types == {"ct2018", "sgk", "sbt"}

    report = CorpusAuditor(raw_dir=raw_dir).audit(manifest_path)
    assert report.all_passed is True, f"Audit failed: {report.model_dump_json(indent=2)}"
