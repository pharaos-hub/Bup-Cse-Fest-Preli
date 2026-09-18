"""
Deterministic Guardrail Validator for GridWise.
Validates and sanitizes LLM-generated directive interpretations before feeding to the optimizer.
"""

import math
from typing import Any, Dict, List, Optional, Tuple

from models import DirectiveInterpretation, DirectiveType


class GuardrailValidationError(Exception):
    """Raised when an interpretation violates strict guardrails and cannot be safely used."""
    pass


VALID_DIRECTIVE_TYPES = {d.value for d in DirectiveType}


def validate_hours_list(hours: Any) -> Tuple[bool, Optional[str]]:
    """
    Validates that hours is a non-empty list of unique, strictly ascending integers in [0, 23].
    """
    if not isinstance(hours, list):
        return False, "hours must be a list"
    if len(hours) == 0:
        return False, "hours list cannot be empty for time-bounded directives"
    
    for h in hours:
        if not isinstance(h, int) or isinstance(h, bool):
            return False, f"hour element '{h}' is not an integer"
        if h < 0 or h > 23:
            return False, f"hour '{h}' is outside allowed range [0, 23]"
            
    # Check duplicate and strictly ascending
    if len(hours) != len(set(hours)):
        return False, f"duplicate hours found: {hours}"
    if hours != sorted(hours):
        return False, f"hours must be strictly ascending, got: {hours}"
        
    return True, None


def validate_interpretation(
    interp: DirectiveInterpretation,
    expected_note_index: int,
    num_notes: int
) -> Tuple[bool, Optional[str]]:
    """
    Performs deterministic validation of a single DirectiveInterpretation.
    Returns (is_valid, error_reason).
    """
    # 1. Map to exactly one existing note_index
    if not isinstance(interp.note_index, int) or isinstance(interp.note_index, bool):
        return False, f"note_index {interp.note_index} is not an integer"
    if interp.note_index != expected_note_index:
        return False, f"note_index {interp.note_index} does not match expected {expected_note_index}"
    if interp.note_index < 0 or interp.note_index >= num_notes:
        return False, f"note_index {interp.note_index} is out of bounds [0, {num_notes - 1}]"

    # 2. Supported directive type
    raw_type = interp.directive_type.value if isinstance(interp.directive_type, DirectiveType) else str(interp.directive_type)
    if raw_type not in VALID_DIRECTIVE_TYPES:
        return False, f"unsupported directive_type: '{raw_type}'"

    # 3. Consistency between applies and directive_type
    if raw_type == DirectiveType.NO_OP.value:
        if interp.applies is not False:
            return False, "directive_type 'no_op' must have applies=false"
    else:
        if interp.applies is not True:
            return False, f"directive_type '{raw_type}' must have applies=true"

    adj = interp.structured_adjustment or {}

    # If no_op, adjustment should ideally be empty
    if raw_type == DirectiveType.NO_OP.value:
        return True, None

    # For all operational directives, hours must be present and valid
    if "hours" not in adj:
        return False, f"missing required field 'hours' in structured_adjustment for '{raw_type}'"
    
    hours_valid, hours_err = validate_hours_list(adj["hours"])
    if not hours_valid:
        return False, hours_err

    # 4. Directive specific parameters
    if raw_type == DirectiveType.SOLAR_REDUCTION.value:
        if "factor" not in adj:
            return False, "missing 'factor' in structured_adjustment for solar_reduction"
        factor = adj["factor"]
        if not isinstance(factor, (int, float)) or isinstance(factor, bool):
            return False, f"solar_reduction factor must be a number, got: {type(factor).__name__}"
        if not math.isfinite(factor):
            return False, "solar_reduction factor must be finite"
        if factor < 0.0 or factor > 1.0:
            return False, f"solar_reduction factor {factor} must be in [0.0, 1.0]"

    elif raw_type == DirectiveType.MINIMUM_BATTERY_RESERVE.value:
        if "min_reserve_kwh" not in adj:
            return False, "missing 'min_reserve_kwh' in structured_adjustment for minimum_battery_reserve"
        min_reserve = adj["min_reserve_kwh"]
        if not isinstance(min_reserve, (int, float)) or isinstance(min_reserve, bool):
            return False, "min_reserve_kwh must be a number"
        if not math.isfinite(min_reserve) or min_reserve < 0.0:
            return False, f"min_reserve_kwh must be non-negative and finite, got: {min_reserve}"

    elif raw_type == DirectiveType.MAX_GRID_WINDOW.value:
        if "max_grid_kwh" not in adj:
            return False, "missing 'max_grid_kwh' in structured_adjustment for max_grid_window"
        max_grid = adj["max_grid_kwh"]
        if not isinstance(max_grid, (int, float)) or isinstance(max_grid, bool):
            return False, "max_grid_kwh must be a number"
        if not math.isfinite(max_grid) or max_grid < 0.0:
            return False, f"max_grid_kwh must be non-negative and finite, got: {max_grid}"

    elif raw_type in (DirectiveType.NO_CHARGE_WINDOW.value, DirectiveType.NO_DISCHARGE_WINDOW.value):
        # Already verified hours list
        pass

    return True, None


def sanitize_or_reject_interpretation(
    interp: DirectiveInterpretation,
    expected_note_index: int,
    num_notes: int,
    fail_safe: bool = True
) -> DirectiveInterpretation:
    """
    Validates the interpretation.
    If valid, returns it as-is.
    If invalid and fail_safe is True, returns a safe no_op interpretation.
    If invalid and fail_safe is False, raises GuardrailValidationError.
    """
    is_valid, error_reason = validate_interpretation(interp, expected_note_index, num_notes)
    if is_valid:
        return interp

    if not fail_safe:
        raise GuardrailValidationError(f"Guardrail validation failed for note {expected_note_index}: {error_reason}")

    # Fail-safe fallback: convert to harmless no_op
    return DirectiveInterpretation(
        note_index=expected_note_index,
        applies=False,
        directive_type=DirectiveType.NO_OP,
        structured_adjustment={},
        explanation=f"Guardrail sanitized: {error_reason}"
    )


def validate_all_interpretations(
    interpretations: List[DirectiveInterpretation],
    num_notes: int,
    fail_safe: bool = True
) -> List[DirectiveInterpretation]:
    """
    Validates a list of interpretations for all operator notes.
    Ensures 1:1 mapping and correct indexing in note_index order.
    """
    if len(interpretations) != num_notes:
        if not fail_safe:
            raise GuardrailValidationError(f"Expected {num_notes} interpretations, got {len(interpretations)}")
    
    interp_map = {i.note_index: i for i in interpretations if isinstance(i.note_index, int)}
    sanitized: List[DirectiveInterpretation] = []

    for idx in range(num_notes):
        candidate = interp_map.get(idx)
        if candidate is None:
            if not fail_safe:
                raise GuardrailValidationError(f"Missing interpretation for note_index {idx}")
            sanitized.append(
                DirectiveInterpretation(
                    note_index=idx,
                    applies=False,
                    directive_type=DirectiveType.NO_OP,
                    structured_adjustment={},
                    explanation=f"Guardrail sanitized: missing interpretation for note {idx}"
                )
            )
        else:
            sanitized.append(
                sanitize_or_reject_interpretation(candidate, idx, num_notes, fail_safe=fail_safe)
            )

    return sanitized
