"""Hierarchical Curriculum Chunker with metadata enrichment and code/table preservation."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from src.rag.config import RAGConfig
from src.rag.exceptions import ChunkingError
from src.rag.interfaces import DocumentChunker
from src.rag.models import (
    ChunkMetadata,
    ContentType,
    DocType,
    DocumentChunk,
    ParsedPage,
)
from src.rag.taxonomy import TaxonomyProvider, TaxonomyRepository


@dataclass
class _MarkdownBlock:
    """Atomic block of markdown content that must not be split internally."""

    block_type: str  # "heading", "code", "table", "text"
    content: str
    heading_level: int = 0
    heading_text: str = ""


class HierarchicalCurriculumChunker(DocumentChunker):
    """Hierarchical chunker that respects curriculum headings, code blocks, and tables.

    Implements DocumentChunker protocol. Splits Markdown content along curriculum
    hierarchies (Lesson -> Section -> Sub-section/Activity -> Blocks), ensuring
    fenced Python code blocks and Markdown tables remain intact, while enriching
    every chunk with deterministic ID, curriculum metadata, and YCCD identifiers.
    """

    def __init__(
        self,
        config: RAGConfig | None = None,
        taxonomy_provider: TaxonomyProvider | None = None,
        max_chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> None:
        self.config = config or RAGConfig()
        self.max_chunk_size = max_chunk_size or self.config.default_chunk_size
        self.chunk_overlap = chunk_overlap or self.config.default_chunk_overlap
        self._taxonomy_provider = taxonomy_provider

        # Lazy initialize taxonomy repository if not injected
        if self._taxonomy_provider is None and self.config.taxonomy_file.is_file():
            try:
                self._taxonomy_provider = TaxonomyRepository.from_json_file(
                    self.config.taxonomy_file
                )
            except Exception:
                self._taxonomy_provider = None

    def chunk_pages(self, pages: Sequence[ParsedPage]) -> list[DocumentChunk]:
        """Split a sequence of parsed pages into enriched, deterministic DocumentChunks."""
        if not pages:
            return []

        all_chunks: list[DocumentChunk] = []
        chunk_counter = 1

        # Track contextual curriculum state across pages
        current_bai_so: int | None = None
        current_ten_bai: str = ""
        current_muc: str = "Nội dung chung"

        for page in pages:
            page_chunks = self._chunk_single_page(
                page=page,
                chunk_counter_start=chunk_counter,
                inherited_bai_so=current_bai_so,
                inherited_ten_bai=current_ten_bai,
                inherited_muc=current_muc,
            )
            if page_chunks:
                last_chunk_meta = page_chunks[-1].metadata
                current_bai_so = last_chunk_meta.bai_so
                current_ten_bai = last_chunk_meta.ten_bai
                current_muc = last_chunk_meta.muc
                chunk_counter += len(page_chunks)
                all_chunks.extend(page_chunks)

        return all_chunks

    def _chunk_single_page(
        self,
        page: ParsedPage,
        chunk_counter_start: int,
        inherited_bai_so: int | None,
        inherited_ten_bai: str,
        inherited_muc: str,
    ) -> list[DocumentChunk]:
        """Process and chunk a single ParsedPage."""
        text = page.markdown_content.strip()
        if not text:
            return []

        # Determine lesson number and title
        bai_so, ten_bai = self._resolve_lesson(page, inherited_bai_so, inherited_ten_bai)
        if bai_so is None or bai_so < 16 or bai_so > 29:
            raise ChunkingError(
                f"Page {page.page_number} ({page.doc_name}) has invalid lesson: {bai_so}. "
                f"Topic F strictly requires lessons in range [16, 29]."
            )

        # Get relevant YCCD IDs for this lesson from taxonomy provider
        yccd_ids = self._resolve_yccd_ids(bai_so)

        # Parse page markdown into atomic blocks (headings, code, table, paragraphs)
        blocks = self._parse_blocks(text)
        if not blocks:
            return []

        chunks: list[DocumentChunk] = []
        current_muc = (inherited_muc if inherited_bai_so == bai_so else "") or "Nội dung chung"
        accumulator: list[str] = []
        current_len = 0
        current_chunk_type: ContentType | None = None
        counter = chunk_counter_start

        for block in blocks:
            if block.block_type == "heading":
                # Flush existing accumulated content before section switch
                if accumulator:
                    chunk = self._build_chunk(
                        content="\n\n".join(accumulator),
                        page=page,
                        bai_so=bai_so,
                        ten_bai=ten_bai,
                        muc=current_muc,
                        loai_noi_dung=current_chunk_type or "ly_thuyet",
                        yccd_ids=yccd_ids,
                        index=counter,
                    )
                    chunks.append(chunk)
                    counter += 1
                    accumulator = []
                    current_len = 0
                    current_chunk_type = None

                # Update current section title
                current_muc = block.heading_text
                current_chunk_type = self._classify_content_type(block.content)
                continue

            # Classify content type of block if not set
            block_type = self._classify_content_type(block.content)
            if current_chunk_type is None:
                current_chunk_type = block_type

            block_len = len(block.content)

            # Atomic protection for code and table: if they are big, they must stay intact
            if block.block_type in ("code", "table"):
                if accumulator:
                    # Flush previous text so table/code gets clean boundary
                    chunk = self._build_chunk(
                        content="\n\n".join(accumulator),
                        page=page,
                        bai_so=bai_so,
                        ten_bai=ten_bai,
                        muc=current_muc,
                        loai_noi_dung=current_chunk_type,
                        yccd_ids=yccd_ids,
                        index=counter,
                    )
                    chunks.append(chunk)
                    counter += 1
                    accumulator = []
                    current_len = 0

                # Code or table becomes an atomic chunk (never split)
                chunk = self._build_chunk(
                    content=block.content,
                    page=page,
                    bai_so=bai_so,
                    ten_bai=ten_bai,
                    muc=current_muc,
                    loai_noi_dung=block_type,
                    yccd_ids=yccd_ids,
                    index=counter,
                )
                chunks.append(chunk)
                counter += 1
                current_chunk_type = None
                continue

            # For regular text: check if adding exceeds max_chunk_size
            if current_len + block_len > self.max_chunk_size and accumulator:
                chunk = self._build_chunk(
                    content="\n\n".join(accumulator),
                    page=page,
                    bai_so=bai_so,
                    ten_bai=ten_bai,
                    muc=current_muc,
                    loai_noi_dung=current_chunk_type,
                    yccd_ids=yccd_ids,
                    index=counter,
                )
                chunks.append(chunk)
                counter += 1
                accumulator = [block.content]
                current_len = block_len
                current_chunk_type = block_type
            else:
                accumulator.append(block.content)
                current_len += block_len

        # Flush remaining accumulator
        if accumulator:
            chunk = self._build_chunk(
                content="\n\n".join(accumulator),
                page=page,
                bai_so=bai_so,
                ten_bai=ten_bai,
                muc=current_muc,
                loai_noi_dung=current_chunk_type or "ly_thuyet",
                yccd_ids=yccd_ids,
                index=counter,
            )
            chunks.append(chunk)

        return chunks

    def _resolve_lesson(
        self,
        page: ParsedPage,
        inherited_bai_so: int | None,
        inherited_ten_bai: str,
    ) -> tuple[int | None, str]:
        """Resolve lesson number (16-29) and title from page, taxonomy, or context."""
        bai_so = page.bai_so or inherited_bai_so
        ten_bai = page.ten_bai or inherited_ten_bai

        if bai_so is None:
            match = re.search(r"BÀI\s+(\d+)\s*[:.-]\s*(.+)", page.markdown_content, re.IGNORECASE)
            if match:
                bai_so = int(match.group(1))
                if not ten_bai:
                    ten_bai = match.group(2).strip()

        # Fallback to taxonomy provider by page number if available
        if bai_so is None and page.doc_type == "sgk" and self._taxonomy_provider is not None:
            for candidate in range(16, 30):
                try:
                    lesson = self._taxonomy_provider.get_lesson(candidate)
                    if lesson.sgk_page_start <= page.page_number <= lesson.sgk_page_end:
                        bai_so = candidate
                        if not ten_bai:
                            ten_bai = lesson.ten_bai
                        break
                except KeyError:
                    continue

        # Fill lesson title from taxonomy if missing
        if bai_so is not None and not ten_bai and self._taxonomy_provider is not None:
            try:
                lesson = self._taxonomy_provider.get_lesson(bai_so)
                ten_bai = lesson.ten_bai
            except KeyError:
                pass

        if not ten_bai:
            ten_bai = f"Bài {bai_so}" if bai_so else "Chủ đề F: Lập trình Python"

        return bai_so, ten_bai

    def _resolve_yccd_ids(self, bai_so: int) -> list[str]:
        """Fetch list of YCCD IDs for a lesson."""
        if self._taxonomy_provider is not None:
            try:
                return self._taxonomy_provider.get_yccd_ids_for_lesson(bai_so)
            except Exception:
                pass
        return [f"F10.B{bai_so}.YC1"]

    def _parse_blocks(self, text: str) -> list[_MarkdownBlock]:
        """Split markdown text into atomic blocks without breaking code or tables."""
        lines = [line for line in text.splitlines() if line.strip() != "<!-- image -->"]
        blocks: list[_MarkdownBlock] = []

        i = 0
        n = len(lines)
        while i < n:
            line = lines[i]
            stripped = line.strip()

            if not stripped:
                i += 1
                continue

            # Fenced code block (``` ... ```)
            if stripped.startswith("```"):
                code_lines = [line]
                i += 1
                while i < n:
                    code_lines.append(lines[i])
                    if lines[i].strip().startswith("```"):
                        i += 1
                        break
                    i += 1
                blocks.append(_MarkdownBlock(block_type="code", content="\n".join(code_lines)))
                continue

            # Markdown table (line starts and ends with | or contains |)
            if stripped.startswith("|") and stripped.endswith("|"):
                table_lines = [line]
                i += 1
                while i < n:
                    next_strip = lines[i].strip()
                    if next_strip.startswith("|") and next_strip.endswith("|"):
                        table_lines.append(lines[i])
                        i += 1
                    else:
                        break
                blocks.append(_MarkdownBlock(block_type="table", content="\n".join(table_lines)))
                continue

            # Markdown heading (#, ##, ###, ####)
            if stripped.startswith("#"):
                heading_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
                if heading_match:
                    level = len(heading_match.group(1))
                    htext = heading_match.group(2).strip()
                    blocks.append(
                        _MarkdownBlock(
                            block_type="heading",
                            content=line,
                            heading_level=level,
                            heading_text=htext,
                        )
                    )
                    i += 1
                    continue

            # Regular text paragraph / list
            para_lines = [line]
            i += 1
            while i < n:
                next_strip = lines[i].strip()
                if not next_strip:
                    i += 1
                    break
                if next_strip.startswith("```") or (
                    next_strip.startswith("|") and next_strip.endswith("|")
                ) or next_strip.startswith("#"):
                    break
                para_lines.append(lines[i])
                i += 1

            blocks.append(_MarkdownBlock(block_type="text", content="\n".join(para_lines)))

        return blocks

    def _classify_content_type(self, text: str) -> ContentType:
        """Classify section or block into ContentType."""
        lower = text.lower()

        # Check for YCCD / Learning Objectives
        if any(kw in lower for kw in ["yêu cầu cần đạt", "sau bài học này", "mục tiêu bài học"]):
            return "yccd"

        # Check for Exercises / Practice
        if any(
            kw in lower
            for kw in [
                "luyện tập",
                "vận dụng",
                "bài tập",
                "câu hỏi và bài tập",
                "thực hành",
                "nhiệm vụ",
                "hoạt động",
            ]
        ):
            return "bai_tap"

        # Check for Examples / Code demonstration
        if any(
            kw in lower
            for kw in [
                "ví dụ",
                "quan sát ví dụ",
                "chạy thử chương trình",
                "chương trình sau",
                "minh họa",
            ]
        ):
            return "vi_du"

        if "```python" in text:
            return "vi_du"

        return "ly_thuyet"

    def _build_chunk(
        self,
        content: str,
        page: ParsedPage,
        bai_so: int,
        ten_bai: str,
        muc: str,
        loai_noi_dung: ContentType,
        yccd_ids: list[str],
        index: int,
    ) -> DocumentChunk:
        """Construct a validated DocumentChunk with deterministic ID and metadata."""
        clean_content = content.strip()
        doc_type: DocType = page.doc_type
        page_num = page.page_number

        chunk_id = f"{doc_type}_b{bai_so:02d}_p{page_num:04d}_c{index:03d}"
        token_count = len(clean_content.split())

        metadata = ChunkMetadata(
            doc_type=doc_type,
            doc_name=page.doc_name,
            chuong="Chủ đề F: Giải quyết vấn đề với sự trợ giúp của máy tính",
            bai_so=bai_so,
            ten_bai=ten_bai,
            muc=muc or "Nội dung bài học",
            page_number=page_num,
            loai_noi_dung=loai_noi_dung,
            yccd_ids=yccd_ids,
        )

        return DocumentChunk(
            chunk_id=chunk_id,
            content=clean_content,
            metadata=metadata,
            token_count=token_count,
        )

    def export_chunks_to_jsonl(
        self,
        chunks: Sequence[DocumentChunk],
        output_path: Path | None = None,
    ) -> Path:
        """Export chunks into JSONL file (defaults to data/processed/chunks.jsonl)."""
        dest = output_path or (self.config.processed_dir / "chunks.jsonl")
        dest.parent.mkdir(parents=True, exist_ok=True)

        with dest.open("w", encoding="utf-8") as f:
            for chunk in chunks:
                f.write(json.dumps(chunk.model_dump(), ensure_ascii=False) + "\n")

        return dest
