"""Tests for YCCD & CS competency taxonomy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from src.rag.taxonomy import (
    MAX_LESSON_NUMBER,
    MIN_LESSON_NUMBER,
    TaxonomyRepository,
    YCCDTaxonomy,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TAXONOMY_PATH = PROJECT_ROOT / "data" / "yccd_taxonomy.json"


def test_real_yccd_taxonomy_loads_and_covers_lessons_16_to_29() -> None:
    repo = TaxonomyRepository.from_json_file(TAXONOMY_PATH)
    lessons = repo.taxonomy.lessons

    assert len(lessons) == 14
    assert [lesson.bai_so for lesson in lessons] == list(
        range(MIN_LESSON_NUMBER, MAX_LESSON_NUMBER + 1)
    )
    assert "if" in repo.get_lesson(19).ten_bai.lower()
    assert "for" in repo.get_lesson(20).ten_bai.lower()
    assert "while" in repo.get_lesson(21).ten_bai.lower()


def test_taxonomy_yccd_ids_unique_and_queryable() -> None:
    repo = TaxonomyRepository.from_json_file(TAXONOMY_PATH)
    all_ids = [
        item.yccd_id
        for lesson in repo.taxonomy.lessons
        for item in lesson.yccd_items
    ]
    assert len(all_ids) == len(set(all_ids))
    assert len(all_ids) >= 28

    b20_ids = repo.get_yccd_ids_for_lesson(20)
    assert b20_ids == ["F10.B20.YC1", "F10.B20.YC2"]
    entry = repo.get_yccd_by_id("F10.B20.YC1")
    assert entry is not None
    assert "for" in " ".join(entry.keywords).lower()


def test_get_lessons_up_to_enforces_scope_invariant() -> None:
    repo = TaxonomyRepository.from_json_file(TAXONOMY_PATH)
    subset = repo.get_lessons_up_to(20)

    assert [lesson.bai_so for lesson in subset] == [16, 17, 18, 19, 20]
    assert all(lesson.bai_so <= 20 for lesson in subset)

    with pytest.raises(ValueError, match="target_bai"):
        repo.get_lessons_up_to(30)


def test_taxonomy_rejects_duplicate_yccd_id() -> None:
    raw = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    raw["lessons"][0]["yccd_items"][1]["yccd_id"] = raw["lessons"][0]["yccd_items"][0]["yccd_id"]

    with pytest.raises(ValueError, match="Duplicate yccd_id"):
        YCCDTaxonomy.model_validate(raw)


def test_taxonomy_rejects_wrong_lesson_19_title() -> None:
    raw = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    for lesson in raw["lessons"]:
        if lesson["bai_so"] == 19:
            lesson["ten_bai"] = "Câu lệnh lặp For"

    with pytest.raises(ValueError, match="Lesson 19"):
        YCCDTaxonomy.model_validate(raw)
