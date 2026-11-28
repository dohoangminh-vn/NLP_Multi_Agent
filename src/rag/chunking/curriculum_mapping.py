"""Materialize approved CT2018 quotations for the matching source edition."""

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Protocol

from pypdf import PdfReader

from src.rag.corpus_audit import CorpusDocumentSpec
from src.rag.exceptions import ChunkingError
from src.rag.models import ChunkMetadata, DocumentChunk, ParsedPage
from src.rag.taxonomy import TaxonomyProvider


class CurriculumSource(Protocol):
    def read(self, path: Path, spec: CorpusDocumentSpec) -> tuple[ParsedPage, list[DocumentChunk]]: ...


class MappedCurriculumSource:
    def __init__(self, mapping_file: Path, taxonomy: TaxonomyProvider):
        self.mapping_file = mapping_file
        self.taxonomy = taxonomy

    def read(self, path: Path, spec: CorpusDocumentSpec) -> tuple[ParsedPage, list[DocumentChunk]]:
        mapping = json.loads(self.mapping_file.read_text(encoding="utf-8"))
        source = mapping["source"]
        page = source["pdf_page"]
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if mapping["status"] != "approved_by_tv3" or digest != source["sha256"]:
            raise ChunkingError("CT2018 mapping approval or source checksum mismatch")
        if spec.doc_id != source["doc_id"] or not spec.page_range.start_page <= page <= spec.page_range.end_page:
            raise ChunkingError("CT2018 mapping does not cover the selected document and page range")
        text = PdfReader(path).pages[page - 1].extract_text()
        _, start, tail = text.partition("Chủ đề F.")
        table, end, _ = tail.partition("Chủ đề G.")
        if not start or not end:
            raise ChunkingError(f"Missing Topic F table boundaries on CT2018 page {page}")
        normalized = "".join(table.split())
        chunks = []
        if [row["bai_so"] for row in mapping["lessons"]] != list(range(16, 30)):
            raise ChunkingError("CT2018 mapping must cover each lesson 16–29 once")
        for row in mapping["lessons"]:
            lesson = self.taxonomy.get_lesson(row["bai_so"])
            groups = defaultdict(list)
            links = row["links"]
            if sorted(link["yccd_id"] for link in links) != sorted(item.yccd_id for item in lesson.yccd_items):
                raise ChunkingError(f"YCCD links do not match taxonomy for lesson {lesson.bai_so}")
            for link in links:
                groups[link["anchor"]].append(link["yccd_id"])
            for key, ids in groups.items():
                anchor = mapping["anchors"][key]
                quote = anchor["quote"]
                if "".join(quote.split()) not in normalized:
                    raise ChunkingError(f"Missing source anchor {key!r} on CT2018 page {page}")
                if anchor["column"] == "Nội dung":
                    continue
                metadata = ChunkMetadata(
                    doc_type="ct2018", doc_name=spec.doc_name, bai_so=lesson.bai_so,
                    ten_bai=lesson.ten_bai, page_number=page, muc=source["section"],
                    loai_noi_dung="yccd", yccd_ids=ids,
                )
                chunks.append(DocumentChunk(
                    chunk_id=f"{spec.doc_id}_p{page}_b{lesson.bai_so}_{key}",
                    metadata=metadata, content=quote, token_count=len(quote.split()),
                ))
        excerpt = ParsedPage(doc_type="ct2018", doc_name=spec.doc_name, page_number=page,
                             markdown_content="\n\n".join(dict.fromkeys(c.content for c in chunks)))
        return excerpt, chunks
