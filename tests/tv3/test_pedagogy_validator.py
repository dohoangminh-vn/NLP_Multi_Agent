from types import SimpleNamespace
import pytest
from src.utils.pedagogy_validator import (
    ACTIVITIES, PedagogyValidationError, validate_lesson_number,
    validate_scope, validate_ratios, validate_activity_minutes,
    validate_activity_titles, validate_lesson_request, validate_outline,
)


def request(**changes):
    return dict(target_bai=20, scope_mode="90m_full", target_minutes=90,
                basic_ratio=.25, standard_ratio=.5, advanced_ratio=.25, **{}) | changes


def outline(**changes):
    return dict(objective_summary="Sử dụng for", activity_titles=list(ACTIVITIES),
                activity_minutes=[10, 30, 32, 18],
                worksheet_goals=["Đọc code", "Viết code", "Phân tích lỗi"]) | changes


@pytest.mark.parametrize("bai", [16, 20, 29])
def test_valid_lesson(bai):
    validate_lesson_number(bai)


@pytest.mark.parametrize("bai", [15, 30, True, 20.0, "20"])
def test_invalid_lesson(bai):
    with pytest.raises(PedagogyValidationError):
        validate_lesson_number(bai)


@pytest.mark.parametrize("mode,minutes", [("45m_theory",45),("45m_practice",45),("90m_full",90)])
def test_valid_scope(mode, minutes):
    validate_scope(mode, minutes)


@pytest.mark.parametrize("mode,minutes", [("90m_full",45),("45m_theory",90),("other",45),("45m_practice",1),("45m_theory",45.0)])
def test_invalid_scope(mode, minutes):
    with pytest.raises(PedagogyValidationError):
        validate_scope(mode, minutes)


@pytest.mark.parametrize("ratios", [(0.25,.5,.25),(1,0,0),(0,0,1),(.1,.2,.7)])
def test_valid_ratios(ratios):
    validate_ratios(*ratios)


@pytest.mark.parametrize("ratios", [(0,0,0),(.2,.2,.2),(-.1,.6,.5),(25,50,25),(float('nan'),.5,.5),(float('inf'),0,0),(True,0,0),(".5",.5,0)])
def test_invalid_ratios(ratios):
    with pytest.raises(PedagogyValidationError):
        validate_ratios(*ratios)


@pytest.mark.parametrize("minutes", [[-5,20,20,10],[0,15,20,10],[5,20,10,5],[5,20,20],[5,20,10,10.0]])
def test_invalid_minutes(minutes):
    with pytest.raises(PedagogyValidationError):
        validate_activity_minutes(minutes,45)


def test_valid_minutes():
    validate_activity_minutes([5,20,12,8],45)
    validate_activity_minutes([10,30,32,18],90)


@pytest.mark.parametrize("titles", [["A","B","C","D"],list(reversed(ACTIVITIES)),["Khởi động giả",*ACTIVITIES[1:]],list(ACTIVITIES[:3])])
def test_invalid_titles(titles):
    with pytest.raises(PedagogyValidationError):
        validate_activity_titles(titles)


def test_teacher_titles():
    validate_activity_titles([f"Hoạt động {i}: {t} – Ví dụ Python" for i,t in enumerate(ACTIVITIES,1)])


def test_request_and_outline_dict_or_object():
    validate_outline(outline(), request())
    validate_outline(SimpleNamespace(**outline()), SimpleNamespace(**request()))


@pytest.mark.parametrize("changes", [{"objective_summary":" "},{"worksheet_goals":["a","b"]},{"worksheet_goals":["a"," ","c"]}])
def test_invalid_outline(changes):
    with pytest.raises(PedagogyValidationError):
        validate_outline(outline(**changes),request())


def test_missing_field():
    with pytest.raises(PedagogyValidationError, match="target_bai"):
        validate_lesson_request({})
