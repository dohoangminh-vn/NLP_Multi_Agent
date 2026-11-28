"""Corpus manifest schema and PDF integrity auditor for raw source documents."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field, model_validator
from pypdf import PdfReader

logger = logging.getLogger(__name__)

DocType = Literal["ct2018", "sgk", "sbt"]


class PageRange(BaseModel):
    """Inclusive 1-based page range within a source PDF."""

    start_page: int = Field(..., ge=1)
    end_page: int = Field(..., ge=1)

    @model_validator(mode="after")
    def validate_bounds(self) -> PageRange:
        if self.end_page < self.start_page:
            raise ValueError(
                f"end_page ({self.end_page}) must be >= start_page ({self.start_page})"
            )
        return self


class CorpusDocumentSpec(BaseModel):
    """Manifest entry describing a single source PDF document."""

    doc_id: str = Field(..., min_length=1)
    doc_type: DocType
    doc_name: str = Field(..., min_length=1)
    filename: str = Field(..., min_length=1)
    page_range: PageRange
    notes: str = ""


class CorpusManifest(BaseModel):
    """Root manifest describing all required raw PDFs for the MVP scope."""

    version: str = Field(..., min_length=1)
    scope: str = Field(..., min_length=1)
    documents: list[CorpusDocumentSpec] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> CorpusManifest:
        ids = [doc.doc_id for doc in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate doc_id found in corpus manifest.")
        return self


class DocumentAuditResult(BaseModel):
    """Audit outcome for a single document in the manifest."""

    doc_id: str
    doc_type: DocType
    filename: str
    exists: bool
    readable: bool
    actual_pages: int | None = None
    expected_range: PageRange
    range_valid: bool
    sha256: str | None = None
    error: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.exists and self.readable and self.range_valid and self.error is None


class CorpusAuditReport(BaseModel):
    """Aggregated audit report for the entire raw PDF corpus."""

    manifest_path: str
    total_documents: int
    passed_documents: int
    all_passed: bool
    results: list[DocumentAuditResult]


class PDFInspector(Protocol):
    """Abstraction for reading PDF page count so auditing can be unit tested cleanly."""

    def get_page_count(self, pdf_path: Path) -> int:
        """Return total page count of the PDF or raise an exception if unreadable."""
        ...


class PyPDFInspector:
    """Default PDFInspector implementation backed by pypdf."""

    def get_page_count(self, pdf_path: Path) -> int:
        reader = PdfReader(str(pdf_path))
        return len(reader.pages)


def _compute_sha256(file_path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


class CorpusAuditor:
    """Validates raw PDF corpus files against a CorpusManifest."""

    def __init__(self, raw_dir: Path, inspector: PDFInspector | None = None) -> None:
        self._raw_dir = raw_dir
        self._inspector: PDFInspector = inspector or PyPDFInspector()

    def load_manifest(self, manifest_path: Path) -> CorpusManifest:
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Manifest file not found: {manifest_path}")
        raw_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return CorpusManifest.model_validate(raw_data)

    def audit(self, manifest_path: Path) -> CorpusAuditReport:
        manifest = self.load_manifest(manifest_path)
        results: list[DocumentAuditResult] = []

        for spec in manifest.documents:
            results.append(self._audit_single(spec))

        passed = sum(1 for item in results if item.is_valid)
        return CorpusAuditReport(
            manifest_path=str(manifest_path),
            total_documents=len(results),
            passed_documents=passed,
            all_passed=(passed == len(results)),
            results=results,
        )

    def _audit_single(self, spec: CorpusDocumentSpec) -> DocumentAuditResult:
        pdf_path = self._raw_dir / spec.filename
        if not pdf_path.is_file():
            msg = f"Missing source PDF: {pdf_path}"
            logger.warning(msg)
            return DocumentAuditResult(
                doc_id=spec.doc_id,
                doc_type=spec.doc_type,
                filename=spec.filename,
                exists=False,
                readable=False,
                expected_range=spec.page_range,
                range_valid=False,
                error=msg,
            )

        try:
            page_count = self._inspector.get_page_count(pdf_path)
        except Exception as exc:  # noqa: BLE001
            msg = f"Failed to read PDF '{spec.filename}': {exc}"
            logger.error(msg)
            return DocumentAuditResult(
                doc_id=spec.doc_id,
                doc_type=spec.doc_type,
                filename=spec.filename,
                exists=True,
                readable=False,
                expected_range=spec.page_range,
                range_valid=False,
                error=msg,
            )

        range_valid = spec.page_range.end_page <= page_count
        error_msg: str | None = None
        if not range_valid:
            error_msg = (
                f"Configured end_page ({spec.page_range.end_page}) exceeds "
                f"actual page count ({page_count}) in '{spec.filename}'."
            )
            logger.error(error_msg)

        checksum = _compute_sha256(pdf_path)
        return DocumentAuditResult(
            doc_id=spec.doc_id,
            doc_type=spec.doc_type,
            filename=spec.filename,
            exists=True,
            readable=True,
            actual_pages=page_count,
            expected_range=spec.page_range,
            range_valid=range_valid,
            sha256=checksum,
            error=error_msg,
        )


def main() -> int:
    """CLI entrypoint to audit data/raw_pdfs/manifest.json."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    raw_dir = Path("data/raw_pdfs")
    manifest_path = raw_dir / "manifest.json"
    auditor = CorpusAuditor(raw_dir=raw_dir)
    report = auditor.audit(manifest_path)

    for item in report.results:
        status = "PASS" if item.is_valid else "FAIL"
        pages = item.actual_pages if item.actual_pages is not None else "N/A"
        print(
            f"[{status}] {item.doc_id} ({item.doc_type}) | {item.filename} | "
            f"pages={pages} | target={item.expected_range.start_page}-{item.expected_range.end_page}"
        )
        if item.error:
            print(f"       Error: {item.error}")

    print(f"Summary: {report.passed_documents}/{report.total_documents} documents valid.")
    return 0 if report.all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
