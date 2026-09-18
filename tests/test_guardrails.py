"""
Unit tests for guardrails validation and sanitization.
"""

import pytest
from guardrails import (
    GuardrailValidationError,
    sanitize_or_reject_interpretation,
    validate_all_interpretations,
    validate_hours_list,
    validate_interpretation,
)
from models import DirectiveInterpretation, DirectiveType


def test_valid_directives_all_types():
    """Ensure all 6 directive types pass guardrails when valid."""
    cases = [
        DirectiveInterpretation(
            note_index=0,
            applies=True,
            directive_type=DirectiveType.SOLAR_REDUCTION,
            structured_adjustment={"hours": [11, 12, 13], "factor": 0.3},
            explanation="Valid solar reduction"
        ),
        DirectiveInterpretation(
            note_index=0,
            applies=True,
            directive_type=DirectiveType.MINIMUM_BATTERY_RESERVE,
            structured_adjustment={"hours": [18, 19, 20], "min_reserve_kwh": 35.0},
            explanation="Valid battery reserve"
        ),
        DirectiveInterpretation(
            note_index=0,
            applies=True,
            directive_type=DirectiveType.NO_CHARGE_WINDOW,
            structured_adjustment={"hours": [14, 15]},
            explanation="Valid no charge window"
        ),
        DirectiveInterpretation(
            note_index=0,
            applies=True,
            directive_type=DirectiveType.NO_DISCHARGE_WINDOW,
            structured_adjustment={"hours": [7, 8]},
            explanation="Valid no discharge window"
        ),
        DirectiveInterpretation(
            note_index=0,
            applies=True,
            directive_type=DirectiveType.MAX_GRID_WINDOW,
            structured_adjustment={"hours": [17, 18, 19], "max_grid_kwh": 40.0},
            explanation="Valid max grid window"
        ),
        DirectiveInterpretation(
            note_index=0,
            applies=False,
            directive_type=DirectiveType.NO_OP,
            structured_adjustment={},
            explanation="Valid distractor"
        ),
    ]

    for c in cases:
        valid, err = validate_interpretation(c, expected_note_index=0, num_notes=1)
        assert valid, f"Expected {c.directive_type} to be valid, got: {err}"


def test_hours_validation():
    """Test strict requirements on hours list."""
    # Valid
    ok, _ = validate_hours_list([0, 1, 23])
    assert ok

    # Empty list
    ok, err = validate_hours_list([])
    assert not ok
    assert "empty" in err

    # Non-ascending
    ok, err = validate_hours_list([13, 12])
    assert not ok
    assert "ascending" in err

    # Duplicates
    ok, err = validate_hours_list([12, 12])
    assert not ok
    assert "duplicate" in err

    # Out of range
    ok, err = validate_hours_list([-1, 5])
    assert not ok
    ok, err = validate_hours_list([10, 24])
    assert not ok

    # Non-integer
    ok, err = validate_hours_list(["12"])
    assert not ok


def test_applies_and_directive_consistency():
    """Ensure applies=True with no_op and applies=False with non-no_op are rejected."""
    # applies=True with no_op
    bad_no_op = DirectiveInterpretation(
        note_index=0,
        applies=True,
        directive_type=DirectiveType.NO_OP,
        structured_adjustment={},
        explanation="Invalid"
    )
    ok, err = validate_interpretation(bad_no_op, 0, 1)
    assert not ok
    assert "no_op' must have applies=false" in err

    # applies=False with solar_reduction
    bad_solar = DirectiveInterpretation(
        note_index=0,
        applies=False,
        directive_type=DirectiveType.SOLAR_REDUCTION,
        structured_adjustment={"hours": [11, 12], "factor": 0.5},
        explanation="Invalid"
    )
    ok, err = validate_interpretation(bad_solar, 0, 1)
    assert not ok
    assert "must have applies=true" in err


def test_factor_bounds_and_type():
    """Test factor must be in [0.0, 1.0] and finite."""
    # factor > 1.0
    bad_factor_high = DirectiveInterpretation(
        note_index=0,
        applies=True,
        directive_type=DirectiveType.SOLAR_REDUCTION,
        structured_adjustment={"hours": [11, 12], "factor": 1.5},
        explanation="Too high"
    )
    ok, err = validate_interpretation(bad_factor_high, 0, 1)
    assert not ok
    assert "must be in [0.0, 1.0]" in err

    # factor < 0.0
    bad_factor_neg = DirectiveInterpretation(
        note_index=0,
        applies=True,
        directive_type=DirectiveType.SOLAR_REDUCTION,
        structured_adjustment={"hours": [11, 12], "factor": -0.1},
        explanation="Negative factor"
    )
    ok, err = validate_interpretation(bad_factor_neg, 0, 1)
    assert not ok


def test_negative_reserve_and_grid_caps():
    """Test negative reserve or grid cap values are rejected."""
    bad_reserve = DirectiveInterpretation(
        note_index=0,
        applies=True,
        directive_type=DirectiveType.MINIMUM_BATTERY_RESERVE,
        structured_adjustment={"hours": [18, 19], "min_reserve_kwh": -10.0},
        explanation="Negative reserve"
    )
    ok, err = validate_interpretation(bad_reserve, 0, 1)
    assert not ok
    assert "non-negative" in err

    bad_grid = DirectiveInterpretation(
        note_index=0,
        applies=True,
        directive_type=DirectiveType.MAX_GRID_WINDOW,
        structured_adjustment={"hours": [18, 19], "max_grid_kwh": -5.0},
        explanation="Negative grid cap"
    )
    ok, err = validate_interpretation(bad_grid, 0, 1)
    assert not ok
    assert "non-negative" in err


def test_note_index_mapping():
    """Test mapping to exactly one existing note_index."""
    out_of_bounds = DirectiveInterpretation(
        note_index=5,
        applies=False,
        directive_type=DirectiveType.NO_OP,
        structured_adjustment={},
        explanation="Out of bounds"
    )
    ok, err = validate_interpretation(out_of_bounds, expected_note_index=0, num_notes=2)
    assert not ok


def test_fail_safe_sanitization():
    """Ensure malformed LLM outputs are safely converted to no_op without raising when fail_safe=True."""
    malformed = DirectiveInterpretation(
        note_index=0,
        applies=True,
        directive_type=DirectiveType.SOLAR_REDUCTION,
        structured_adjustment={"hours": [14, 13], "factor": 2.5},  # Non-ascending hours and factor > 1
        explanation="Hallucinated"
    )
    sanitized = sanitize_or_reject_interpretation(malformed, expected_note_index=0, num_notes=1, fail_safe=True)
    assert sanitized.applies is False
    assert sanitized.directive_type == DirectiveType.NO_OP
    assert "Guardrail sanitized" in sanitized.explanation

    # When fail_safe is False, it must raise GuardrailValidationError
    with pytest.raises(GuardrailValidationError):
        sanitize_or_reject_interpretation(malformed, expected_note_index=0, num_notes=1, fail_safe=False)
