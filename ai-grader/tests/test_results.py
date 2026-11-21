from app.models import DEFAULT_GRADE_SCALE
from app.services.results import grade_for_percent


def test_grade_scale_boundaries():
    assert grade_for_percent(100, DEFAULT_GRADE_SCALE) == "10"
    assert grade_for_percent(95, DEFAULT_GRADE_SCALE) == "10"
    assert grade_for_percent(94.9, DEFAULT_GRADE_SCALE) == "9"
    assert grade_for_percent(0, DEFAULT_GRADE_SCALE) == "1"


def test_custom_scale_with_levels():
    scale = [[85, "Apguvis padziļināti"], [60, "Apguvis"], [35, "Turpina apgūt"], [0, "Sācis apgūt"]]
    assert grade_for_percent(70, scale) == "Apguvis"
    assert grade_for_percent(10, scale) == "Sācis apgūt"
