"""Unit tests for app.constraints: validation, labels and the offline NL parser."""

import pytest

from app.constraints import (
    constraint_label,
    parse_constraint_row,
    parse_nl,
    serialise_params,
    validate_params,
)


# ------------------------------------------------------------- validate_params

def test_subject_gap_validates_and_normalises():
    out = validate_params("subject_gap", {"subject": "Math", "gap_minutes": 60})
    assert out == {"subject": "math", "gap_minutes": 60}


def test_subject_gap_rejects_bad_subject():
    with pytest.raises(ValueError):
        validate_params("subject_gap", {"subject": "math; DROP TABLE", "gap_minutes": 60})


def test_subject_gap_rejects_out_of_range_gap():
    with pytest.raises(ValueError):
        validate_params("subject_gap", {"subject": "math", "gap_minutes": 5000})


def test_post_fatigue_buffer_defaults():
    out = validate_params("post_fatigue_buffer", {})
    assert out == {"min_fatigue": 2, "buffer_minutes": 60}


def test_interleave_bounds():
    assert validate_params("interleave", {"max_consecutive": 3}) == {"max_consecutive": 3}
    with pytest.raises(ValueError):
        validate_params("interleave", {"max_consecutive": 9})


def test_blocked_day_part_requires_ordered_hours():
    assert validate_params("blocked_day_part", {"start_hour": 12, "end_hour": 13}) == {
        "start_hour": 12,
        "end_hour": 13,
    }
    with pytest.raises(ValueError):
        validate_params("blocked_day_part", {"start_hour": 22, "end_hour": 8})


def test_unknown_kind_rejected():
    with pytest.raises(ValueError):
        validate_params("yolo", {})


def test_constraint_label_roundtrip():
    params = validate_params("subject_gap", {"subject": "math", "gap_minutes": 45})
    label = constraint_label("subject_gap", params)
    assert "math" in label and "45" in label


def test_parse_constraint_row_serialises():
    row = {
        "id": "c1",
        "kind": "interleave",
        "params": serialise_params({"max_consecutive": 2}),
        "hard": 1,
        "label": "lbl",
    }
    parsed = parse_constraint_row(row)
    assert parsed["hard"] is True
    assert parsed["params"] == {"max_consecutive": 2}


# ------------------------------------------------------------------ parse_nl

def test_parse_nl_subject_gap_british_english():
    out = parse_nl("avoid math right after math class")
    assert out is not None
    assert out["kind"] == "subject_gap"
    assert out["params"]["subject"] == "math"
    assert out["params"]["gap_minutes"] == 60


def test_parse_nl_subject_gap_lecture_wording():
    out = parse_nl("Don't study physics right after my physics lecture")
    assert out is not None
    assert out["kind"] == "subject_gap"
    assert out["params"]["subject"] == "physics"


def test_parse_nl_fatigue_after_long_lectures():
    out = parse_nl("no study right after long lectures")
    assert out is not None
    assert out["kind"] == "post_fatigue_buffer"
    assert out["params"]["min_fatigue"] == 2


def test_parse_nl_tired_3h_lecture():
    out = parse_nl("avoid studying right after a tiring 3-hour lecture")
    assert out is not None
    assert out["kind"] == "post_fatigue_buffer"


def test_parse_nl_lunch():
    out = parse_nl("no study at lunch")
    assert out is not None
    assert out["kind"] == "blocked_day_part"


def test_parse_nl_interleave():
    out = parse_nl("no more than 2 consecutive")
    assert out is not None
    assert out["kind"] == "interleave"
    assert out["params"]["max_consecutive"] == 2


def test_parse_nl_habitual_phrasing_uses_event_subject():
    out = parse_nl("avoid studying right after my physics class")
    assert out is not None
    assert out["params"]["subject"] == "physics"


def test_parse_nl_leading_verb_fillers_are_stripped():
    assert parse_nl("avoid doing math right after math class")["params"]["subject"] == "math"
    assert parse_nl("I don't want to study math right after math class")["params"]["subject"] == "math"


def test_parse_nl_garbage_returns_none():
    assert parse_nl("what is due this week?") is None
    assert parse_nl("") is None
