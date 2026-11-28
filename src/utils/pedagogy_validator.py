"""TV3: deterministic pedagogical constraints, independent of shared schemas."""
from collections.abc import Mapping, Sequence
from math import isclose, isfinite
from numbers import Real
import unicodedata
import re

SCOPE_MINUTES = {"45m_theory": 45, "45m_practice": 45, "90m_full": 90}
ACTIVITIES = ("Khởi động", "Hình thành kiến thức", "Luyện tập", "Vận dụng")


class PedagogyValidationError(ValueError):
    """Input or outline violates a project constraint."""


def _field(obj, name):
    try:
        return obj[name] if isinstance(obj, Mapping) else getattr(obj, name)
    except (KeyError, AttributeError) as exc:
        raise PedagogyValidationError(f"Thiếu trường {name}") from exc


def validate_lesson_number(target_bai):
    if type(target_bai) is not int or not 16 <= target_bai <= 29:
        raise PedagogyValidationError("target_bai phải là số nguyên từ 16 đến 29")


def validate_scope(scope_mode, target_minutes):
    if not isinstance(scope_mode, str) or scope_mode not in SCOPE_MINUTES:
        raise PedagogyValidationError("scope_mode không hợp lệ")
    if type(target_minutes) is not int or target_minutes != SCOPE_MINUTES[scope_mode]:
        raise PedagogyValidationError(f"{scope_mode} yêu cầu {SCOPE_MINUTES[scope_mode]} phút")


def validate_ratios(basic_ratio, standard_ratio, advanced_ratio):
    """Contract uses fractions 0..1, not UI percentages 0..100."""
    ratios = (basic_ratio, standard_ratio, advanced_ratio)
    for value in ratios:
        if isinstance(value, bool) or not isinstance(value, Real) or not 0 <= value <= 1 or not isfinite(value):
            raise PedagogyValidationError("Mỗi tỷ lệ phải là số hữu hạn trong [0, 1]")
    if not isclose(sum(ratios), 1.0, rel_tol=0, abs_tol=1e-6):
        raise PedagogyValidationError("Tổng ba tỷ lệ phải bằng 1 (100%)")


def _sequence(values, size, field):
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or len(values) != size:
        raise PedagogyValidationError(f"{field} phải có đúng {size} phần tử")


def validate_activity_minutes(activity_minutes, target_minutes):
    if type(target_minutes) is not int or target_minutes not in (45, 90):
        raise PedagogyValidationError("Thời lượng bài phải là 45 hoặc 90 phút")
    _sequence(activity_minutes, 4, "activity_minutes")
    if any(type(m) is not int or m <= 0 for m in activity_minutes):
        raise PedagogyValidationError("Mỗi hoạt động phải có số phút nguyên dương")
    if sum(activity_minutes) != target_minutes:
        raise PedagogyValidationError("Tổng phút hoạt động không khớp thời lượng bài")


def validate_activity_titles(activity_titles):
    """Allow teacher edits to descriptions, preserving the four phase labels."""
    _sequence(activity_titles, 4, "activity_titles")
    for i, (title, expected) in enumerate(zip(activity_titles, ACTIVITIES), 1):
        if not isinstance(title, str):
            raise PedagogyValidationError(f"Hoạt động {i} phải có tên dạng văn bản")
        text = unicodedata.normalize("NFC", title).strip().casefold()
        text = re.sub(rf"^hoạt động\s+{i}\s*[:.\-–—]\s*", "", text)
        phase = expected.casefold()
        if not re.fullmatch(re.escape(phase) + r"(?:\s*[:\-–—]\s*\S.*)?", text):
            raise PedagogyValidationError(f"Hoạt động {i} phải là {expected}")


def _nonempty(value, field):
    if not isinstance(value, str) or not value.strip():
        raise PedagogyValidationError(f"{field} không được để trống")


def validate_lesson_request(request):
    validate_lesson_number(_field(request, "target_bai"))
    validate_scope(_field(request, "scope_mode"), _field(request, "target_minutes"))
    validate_ratios(*(_field(request, k) for k in ("basic_ratio", "standard_ratio", "advanced_ratio")))


def validate_outline(outline, request):
    validate_lesson_request(request)
    validate_activity_titles(_field(outline, "activity_titles"))
    validate_activity_minutes(_field(outline, "activity_minutes"), _field(request, "target_minutes"))
    _nonempty(_field(outline, "objective_summary"), "objective_summary")
    goals = _field(outline, "worksheet_goals")
    _sequence(goals, 3, "worksheet_goals")
    for i, goal in enumerate(goals, 1):
        _nonempty(goal, f"worksheet_goals[{i}]")
