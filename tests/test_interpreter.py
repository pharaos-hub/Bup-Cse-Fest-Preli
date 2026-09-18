"""
Unit tests for interpreter and natural language parsing.
"""

from interpreter import (
    deterministic_fallback_interpret,
    interpret_all_notes,
    parse_time_window,
)
from models import DirectiveType


def test_parse_time_window():
    """Verify half-open time interval parsing."""
    assert parse_time_window("1 PM to 3 PM") == [13, 14]
    assert parse_time_window("from 8 AM to 11 AM") == [8, 9, 10]
    assert parse_time_window("between 18:00 and 22:00") == [18, 19, 20, 21]
    assert parse_time_window("hours 13 to 15") == [13, 14]


def test_distractor_notes():
    """Ensure irrelevant notes resolve to no_op with applies=False."""
    distractors = [
        "Please remember to log your hours in the staff portal before leaving.",
        "Shift log: Completed routine inspection of inverter panels with no issues.",
        "Security update: visitor badge #401 issued to university audit team.",
        "Coffee break scheduled for maintenance technicians at 3 PM."
    ]
    for idx, text in enumerate(distractors):
        interp = deterministic_fallback_interpret(text, idx)
        assert interp.directive_type == DirectiveType.NO_OP
        assert interp.applies is False
        assert interp.note_index == idx


def test_solar_reduction_factor():
    """Verify factor equals remaining fraction (e.g. 70% drop -> factor 0.30)."""
    note = "Severe dust storm between 11 AM and 3 PM reduces solar by 70%."
    interp = deterministic_fallback_interpret(note, 0)
    assert interp.directive_type == DirectiveType.SOLAR_REDUCTION
    assert interp.applies is True
    assert interp.structured_adjustment["hours"] == [11, 12, 13, 14]
    assert abs(interp.structured_adjustment["factor"] - 0.30) < 1e-4


def test_minimum_battery_reserve():
    """Verify minimum battery reserve parsing."""
    note = "Maintain elevated battery reserve of at least 35 kWh from 6 PM to 10 PM."
    interp = deterministic_fallback_interpret(note, 0)
    assert interp.directive_type == DirectiveType.MINIMUM_BATTERY_RESERVE
    assert interp.applies is True
    assert interp.structured_adjustment["hours"] == [18, 19, 20, 21]
    assert interp.structured_adjustment["min_reserve_kwh"] == 35.0


def test_no_charge_window():
    """Verify no charge window parsing."""
    note = "Do not charge the battery between 2 PM and 5 PM due to transformer maintenance."
    interp = deterministic_fallback_interpret(note, 0)
    assert interp.directive_type == DirectiveType.NO_CHARGE_WINDOW
    assert interp.applies is True
    assert interp.structured_adjustment["hours"] == [14, 15, 16]


def test_no_discharge_window():
    """Verify no discharge window parsing."""
    note = "Grid operator requested no battery discharge from 8 AM to 11 AM."
    interp = deterministic_fallback_interpret(note, 0)
    assert interp.directive_type == DirectiveType.NO_DISCHARGE_WINDOW
    assert interp.applies is True
    assert interp.structured_adjustment["hours"] == [8, 9, 10]


def test_max_grid_window():
    """Verify max grid cap parsing."""
    note = "Substation maintenance: grid import cannot exceed 40 kWh between 5 PM and 8 PM."
    interp = deterministic_fallback_interpret(note, 0)
    assert interp.directive_type == DirectiveType.MAX_GRID_WINDOW
    assert interp.applies is True
    assert interp.structured_adjustment["hours"] == [17, 18, 19]
    assert interp.structured_adjustment["max_grid_kwh"] == 40.0
