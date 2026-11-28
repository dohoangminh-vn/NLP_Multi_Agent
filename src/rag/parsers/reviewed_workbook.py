"""Read page-aligned, visually transcribed exercises and keys for the scanned workbook."""

import hashlib
import json
import os
import re
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from src.rag.corpus_audit import CorpusDocumentSpec
from src.rag.exceptions import ParsingError
from src.rag.models import ChunkMetadata, DocumentChunk, ParsedPage
from src.rag.taxonomy import LessonTaxonomy, TaxonomyProvider

PAGE = re.compile(r"^<!-- (exercises|answers) (\d+) -->$", re.MULTILINE)
ITEM = re.compile(r"^(\d+)\.(\d+)\.\s")
SECTIONS = {"exercises": "Bài tập", "answers": "Đáp án"}


class WorkbookSource(Protocol):
    def read(self, path: Path, spec: CorpusDocumentSpec) -> tuple[list[ParsedPage], list[DocumentChunk]]: ...


class ReviewedWorkbook:
    def __init__(self, directory: Path, taxonomy: TaxonomyProvider, *, output_dir: Path):
        self.directory = directory
        self.taxonomy = taxonomy
        self.figure_dir = Path(os.path.relpath(directory / "figures", output_dir)).as_posix()

    def read(self, path: Path, spec: CorpusDocumentSpec) -> tuple[list[ParsedPage], list[DocumentChunk]]:
        index = json.loads((self.directory / "source.json").read_text(encoding="utf-8"))
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if spec.doc_id != index["doc_id"] or digest != index["sha256"]:
            raise ParsingError("Workbook edition does not match the reviewed transcription")
        if set(map(int, index["items_per_lesson"])) != set(range(16, 30)):
            raise ParsingError("Workbook inventory must contain every lesson 16–29")
        chunks = []
        for lesson, count in index["items_per_lesson"].items():
            chunks.extend(self._read_lesson(self.taxonomy.get_lesson(int(lesson)), count, spec, index["page_ranges"]))
        by_page = defaultdict(list)
        for chunk in chunks:
            by_page[chunk.metadata.page_number].append(chunk)
        expected_pages = {page for start, end in index["page_ranges"].values() for page in range(start, end + 1)}
        if by_page.keys() != expected_pages:
            raise ParsingError("Incomplete workbook page coverage")
        pages = []
        for number, fragments in sorted(by_page.items()):
            content = "\n\n".join(f"## {c.metadata.muc}\n\n{c.content}" for c in fragments)
            lessons = {c.metadata.bai_so for c in fragments}
            pages.append(ParsedPage(
                doc_type="sbt", doc_name=spec.doc_name, page_number=number, markdown_content=content,
                bai_so=fragments[0].metadata.bai_so if len(lessons) == 1 else None,
                ten_bai=fragments[0].metadata.ten_bai if len(lessons) == 1 else None,
                has_code_blocks="```" in content, has_tables=any(line.startswith("|") for line in content.splitlines()),
            ))
        return pages, chunks

    def _read_lesson(self, lesson: LessonTaxonomy, count: int, spec: CorpusDocumentSpec,
                     ranges: dict[str, list[int]]) -> Iterator[DocumentChunk]:
        source = (self.directory / f"{lesson.bai_so}.md").read_text(encoding="utf-8")
        preamble, *parts = PAGE.split(source)
        if preamble.strip() or not parts:
            raise ParsingError(f"Missing workbook page marker for lesson {lesson.bai_so}")
        current = dict.fromkeys(SECTIONS, 0)
        previous = dict.fromkeys(SECTIONS, 0)
        for part, page, body in zip(parts[::3], parts[1::3], parts[2::3], strict=True):
            page = int(page)
            start, end = ranges[part]
            if not (start <= page <= end and spec.page_range.start_page <= page <= spec.page_range.end_page
                    and previous[part] < page):
                raise ParsingError(f"Invalid {part} page {page} in lesson {lesson.bai_so}")
            previous[part] = page
            fragments = defaultdict(list)
            fenced = False
            for line in body.strip("\n").splitlines():
                item = None if fenced else ITEM.match(line)
                if item:
                    current[part] += 1
                    if (int(item[1]), int(item[2])) != (lesson.bai_so, current[part]):
                        raise ParsingError(f"Unexpected {part} item {item[0]!r} on page {page}")
                if line.startswith("```"):
                    fenced = not fenced
                fragments[current[part]].append(line)
            if fenced or not current[part] or not body.strip():
                raise ParsingError(f"Incomplete {part} on page {page}")
            for number, lines in fragments.items():
                content = "\n".join(lines).strip().replace("](figures/", f"]({self.figure_dir}/")
                if not content:
                    continue
                metadata = ChunkMetadata(
                    doc_type="sbt", doc_name=spec.doc_name, bai_so=lesson.bai_so, ten_bai=lesson.ten_bai,
                    page_number=page, muc=f"{SECTIONS[part]} {lesson.bai_so}.{number}", loai_noi_dung="bai_tap",
                    yccd_ids=[item.yccd_id for item in lesson.yccd_items],
                )
                yield DocumentChunk(chunk_id=f"{spec.doc_id}_{part}_{lesson.bai_so}_{number}_p{page}",
                                    metadata=metadata, content=content, token_count=len(content.split()))
        if any(value != count for value in current.values()):
            raise ParsingError(f"Expected {count} exercises and answers for lesson {lesson.bai_so}, got {current}")
