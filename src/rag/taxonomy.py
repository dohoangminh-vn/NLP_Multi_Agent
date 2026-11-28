"""YCCD & CS Competency Taxonomy schema and repository for Topic F (Lessons 16-29)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field, model_validator

CompetencyCode = Literal["NLa", "NLb", "NLc", "NLd", "NLe"]
BloomLevel = Literal["Nhớ", "Hiểu", "Vận dụng", "Phân tích", "Đánh giá", "Sáng tạo"]

MIN_LESSON_NUMBER = 16
MAX_LESSON_NUMBER = 29
EXPECTED_LESSON_RANGE = range(MIN_LESSON_NUMBER, MAX_LESSON_NUMBER + 1)


class YCCDEntry(BaseModel):
    """Single learning objective (Yêu cầu cần đạt) entry mapped to TT32 & Bloom."""

    yccd_id: str = Field(..., pattern=r"^F10\.B(1[6-9]|2[0-9])\.YC\d+$")
    description: str = Field(..., min_length=10)
    competency_codes: list[CompetencyCode] = Field(..., min_length=1)
    bloom_levels: list[BloomLevel] = Field(..., min_length=1)
    keywords: list[str] = Field(..., min_length=1)
    ct2018_page: int = Field(..., ge=1)


class LessonTaxonomy(BaseModel):
    """Taxonomy metadata and YCCDs for a single textbook lesson."""

    bai_so: int = Field(..., ge=MIN_LESSON_NUMBER, le=MAX_LESSON_NUMBER)
    ten_bai: str = Field(..., min_length=3)
    sgk_page_start: int = Field(..., ge=1)
    sgk_page_end: int = Field(..., ge=1)
    ct2018_subtopic: str = Field(..., min_length=3)
    yccd_items: list[YCCDEntry] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_lesson_invariants(self) -> LessonTaxonomy:
        if self.sgk_page_end < self.sgk_page_start:
            raise ValueError(
                f"Lesson {self.bai_so}: sgk_page_end ({self.sgk_page_end}) "
                f"< sgk_page_start ({self.sgk_page_start})."
            )
        expected_prefix = f"F10.B{self.bai_so}."
        for entry in self.yccd_items:
            if not entry.yccd_id.startswith(expected_prefix):
                raise ValueError(
                    f"YCCD '{entry.yccd_id}' does not match lesson prefix '{expected_prefix}'."
                )
        return self


class YCCDTaxonomy(BaseModel):
    """Full YCCD taxonomy covering Lessons 16-29 of CS 10 KNTT Topic F."""

    version: str = Field(..., min_length=1)
    curriculum_source: str = Field(..., min_length=1)
    textbook_source: str = Field(..., min_length=1)
    topic: str = Field(..., min_length=1)
    competencies: dict[CompetencyCode, str]
    lessons: list[LessonTaxonomy] = Field(..., min_length=14, max_length=14)

    @model_validator(mode="after")
    def validate_taxonomy_invariants(self) -> YCCDTaxonomy:
        lesson_numbers = [lesson.bai_so for lesson in self.lessons]
        if sorted(lesson_numbers) != list(EXPECTED_LESSON_RANGE):
            raise ValueError(
                f"Taxonomy must cover lessons 16-29 exactly once, got: {lesson_numbers}"
            )

        seen_yccd_ids: set[str] = set()
        for lesson in self.lessons:
            for item in lesson.yccd_items:
                if item.yccd_id in seen_yccd_ids:
                    raise ValueError(f"Duplicate yccd_id detected: '{item.yccd_id}'")
                seen_yccd_ids.add(item.yccd_id)

        # Domain invariants: Lesson 19 = if, Lesson 20 = for, Lesson 21 = while
        by_num = {lesson.bai_so: lesson for lesson in self.lessons}
        required_titles = {19: "if", 20: "for", 21: "while"}
        for bai_so, token in required_titles.items():
            if token not in by_num[bai_so].ten_bai.lower():
                raise ValueError(
                    f"Lesson {bai_so} title '{by_num[bai_so].ten_bai}' must reference '{token}'."
                )
        return self


class TaxonomyProvider(Protocol):
    """Read-only interface for querying YCCD taxonomy entries."""

    def get_lesson(self, bai_so: int) -> LessonTaxonomy:
        ...

    def get_lessons_up_to(self, target_bai: int) -> list[LessonTaxonomy]:
        ...

    def get_yccd_ids_for_lesson(self, bai_so: int) -> list[str]:
        ...


class TaxonomyRepository:
    """Repository encapsulating access to data/yccd_taxonomy.json."""

    def __init__(self, taxonomy: YCCDTaxonomy) -> None:
        self._taxonomy = taxonomy
        self._by_lesson: dict[int, LessonTaxonomy] = {
            lesson.bai_so: lesson for lesson in taxonomy.lessons
        }
        self._by_yccd_id: dict[str, YCCDEntry] = {
            item.yccd_id: item
            for lesson in taxonomy.lessons
            for item in lesson.yccd_items
        }

    @classmethod
    def from_json_file(cls, path: Path) -> TaxonomyRepository:
        if not path.is_file():
            raise FileNotFoundError(f"Taxonomy file not found: {path}")
        raw_data = json.loads(path.read_text(encoding="utf-8"))
        taxonomy = YCCDTaxonomy.model_validate(raw_data)
        return cls(taxonomy=taxonomy)

    @property
    def taxonomy(self) -> YCCDTaxonomy:
        return self._taxonomy

    def get_lesson(self, bai_so: int) -> LessonTaxonomy:
        if bai_so not in self._by_lesson:
            raise KeyError(f"Lesson {bai_so} is outside Topic F scope (16-29).")
        return self._by_lesson[bai_so]

    def get_lessons_up_to(self, target_bai: int) -> list[LessonTaxonomy]:
        if target_bai < MIN_LESSON_NUMBER or target_bai > MAX_LESSON_NUMBER:
            raise ValueError(
                f"target_bai={target_bai} must be within [{MIN_LESSON_NUMBER}, {MAX_LESSON_NUMBER}]."
            )
        return [
            self._by_lesson[num]
            for num in sorted(self._by_lesson)
            if num <= target_bai
        ]

    def get_yccd_by_id(self, yccd_id: str) -> YCCDEntry | None:
        return self._by_yccd_id.get(yccd_id)

    def get_yccd_ids_for_lesson(self, bai_so: int) -> list[str]:
        return [entry.yccd_id for entry in self.get_lesson(bai_so).yccd_items]
