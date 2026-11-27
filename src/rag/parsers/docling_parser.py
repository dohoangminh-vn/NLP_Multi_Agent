"""Layout-aware PDF Document Parser backed by Docling and MarkdownPostProcessor."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from src.rag.config import RAGConfig
from src.rag.exceptions import ParsingError
from src.rag.models import DocType, ParsedPage
from src.rag.parsers.markdown_postprocessor import MarkdownPostProcessor

logger = logging.getLogger(__name__)


def _detect_doc_type(filename: str) -> tuple[DocType, str]:
    """Infer DocType and friendly doc_name from filename."""
    lower = filename.lower()
    if "sgk" in lower:
        return "sgk", "SGK Tin học 10 - Kết nối tri thức với cuộc sống"
    if "sbt" in lower:
        return "sbt", "SBT Tin học 10 - Kết nối tri thức với cuộc sống"
    if "thông-tư" in lower or "thong-tu" in lower or "32" in lower or "ct2018" in lower:
        return "ct2018", "Chương trình GDPT 2018 môn Tin học (Thông tư 32/2018/TT-BGDĐT)"
    return "sgk", filename


class DoclingDocumentParser:
    """DocumentParser implementation supporting layout retention, page ranges, and metadata export."""

    def __init__(
        self,
        config: RAGConfig | None = None,
        post_processor: MarkdownPostProcessor | None = None,
        use_docling_native: bool = True,
    ) -> None:
        self.config = config or RAGConfig()
        self.post_processor = post_processor or MarkdownPostProcessor()
        self.use_docling_native = use_docling_native
        self._docling_converter: Any = None
        if self.use_docling_native:
            self._init_docling_converter()

    def _init_docling_converter(self) -> None:
        """Lazily initialize Docling DocumentConverter if installed."""
        try:
            from docling.document_converter import DocumentConverter

            self._docling_converter = DocumentConverter()
            logger.info("Docling DocumentConverter initialized successfully.")
        except ImportError:
            logger.warning("Docling is not installed; falling back to layout-aware pypdf engine.")
            self._docling_converter = None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to initialize Docling converter (%s); using fallback engine.", exc)
            self._docling_converter = None

    def parse_document(
        self,
        file_path: Path,
        start_page: int | None = None,
        end_page: int | None = None,
    ) -> list[ParsedPage]:
        """Parse PDF document pages into ParsedPage instances preserving layout and code formatting."""
        if not file_path.is_file():
            raise FileNotFoundError(f"PDF file not found: {file_path}")

        doc_type, doc_name = _detect_doc_type(file_path.name)

        try:
            reader = PdfReader(str(file_path))
            total_pages = len(reader.pages)
        except Exception as exc:
            raise ParsingError(f"Failed to read PDF file '{file_path}': {exc}") from exc

        s_page = max(1, start_page or 1)
        e_page = min(total_pages, end_page or total_pages)
        if s_page > e_page:
            raise ParsingError(f"start_page ({s_page}) must be <= end_page ({e_page})")

        # Try docling native conversion if available and configured
        if self._docling_converter is not None:
            try:
                return self._parse_with_docling(
                    file_path=file_path,
                    doc_type=doc_type,
                    doc_name=doc_name,
                    start_page=s_page,
                    end_page=e_page,
                    reader=reader,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Docling conversion failed (%s); falling back to pypdf layout engine.", exc)

        # Fallback layout-aware extraction
        return self._parse_with_fallback(
            doc_type=doc_type,
            doc_name=doc_name,
            start_page=s_page,
            end_page=e_page,
            reader=reader,
        )

    def _parse_with_docling(
        self,
        file_path: Path,
        doc_type: DocType,
        doc_name: str,
        start_page: int,
        end_page: int,
        reader: PdfReader,
    ) -> list[ParsedPage]:
        """Parse using Docling DocumentConverter."""
        conv_res = self._docling_converter.convert(str(file_path))
        doc = conv_res.document

        pages: list[ParsedPage] = []
        for pno in range(start_page, end_page + 1):
            raw_md = ""
            try:
                # Docling exports markdown per page if supported, or via export_to_markdown
                raw_md = doc.export_to_markdown(page_no=pno)
            except TypeError:
                # Some docling versions export whole doc; fallback to reader text for specific page
                raw_md = reader.pages[pno - 1].extract_text() or ""
            except Exception:  # noqa: BLE001
                raw_md = reader.pages[pno - 1].extract_text() or ""

            res = self.post_processor.process(raw_md)
            pages.append(
                ParsedPage(
                    doc_type=doc_type,
                    doc_name=doc_name,
                    page_number=pno,
                    markdown_content=res.cleaned_markdown or f"# Trang {pno}\n\n(Nội dung trang {pno})",
                    bai_so=res.detected_bai_so,
                    ten_bai=res.detected_ten_bai,
                    has_tables=res.has_tables,
                    has_code_blocks=res.has_code_blocks,
                )
            )
        return pages

    def _parse_with_fallback(
        self,
        doc_type: DocType,
        doc_name: str,
        start_page: int,
        end_page: int,
        reader: PdfReader,
    ) -> list[ParsedPage]:
        """Fallback layout-aware extractor using pypdf and post-processing."""
        pages: list[ParsedPage] = []
        for pno in range(start_page, end_page + 1):
            try:
                raw_text = reader.pages[pno - 1].extract_text() or ""
            except Exception as exc:
                raise ParsingError(f"Error extracting page {pno}: {exc}") from exc

            res = self.post_processor.process(raw_text)
            pages.append(
                ParsedPage(
                    doc_type=doc_type,
                    doc_name=doc_name,
                    page_number=pno,
                    markdown_content=res.cleaned_markdown or f"# Trang {pno}\n\n(Nội dung trang {pno})",
                    bai_so=res.detected_bai_so,
                    ten_bai=res.detected_ten_bai,
                    has_tables=res.has_tables,
                    has_code_blocks=res.has_code_blocks,
                )
            )
        return pages

    def export_pages_to_processed(
        self,
        pages: list[ParsedPage],
        output_dir: Path | None = None,
    ) -> list[Path]:
        """Export parsed pages to data/processed/ as markdown and metadata JSON files."""
        target_dir = output_dir or self.config.processed_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        exported_files: list[Path] = []
        for page in pages:
            stem = f"{page.doc_type}_p{page.page_number:04d}"
            md_path = target_dir / f"{stem}.md"
            meta_path = target_dir / f"{stem}.json"

            md_path.write_text(page.markdown_content, encoding="utf-8")
            meta_data = {
                "doc_type": page.doc_type,
                "doc_name": page.doc_name,
                "page_number": page.page_number,
                "bai_so": page.bai_so,
                "ten_bai": page.ten_bai,
                "has_tables": page.has_tables,
                "has_code_blocks": page.has_code_blocks,
            }
            meta_path.write_text(json.dumps(meta_data, ensure_ascii=False, indent=2), encoding="utf-8")

            exported_files.extend([md_path, meta_path])

        return exported_files
