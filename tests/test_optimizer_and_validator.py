"""
Unit tests for the LP optimizer and post-optimization replay validator.
"""

import json
from pathlib import Path
import pytest

from models import BatteryAction, DirectiveInterpretation, DirectiveType, OptimizeRequest
from optimizer import solve_energy_schedule
from validator import ScheduleConsistencyError, replay_and_validate_schedule


@pytest.fixture
def scenario_101_request() -> OptimizeRequest:
    fixture_path = Path(__file__).parent / "data" / "scenario_grid_101.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return OptimizeRequest(**data)


def test_optimizer_baseline(scenario_101_request: OptimizeRequest):
    """Solve baseline without active directives and verify balance & neutrality."""
    directives = [
        DirectiveInterpretation(
            note_index=0,
            applies=False,
            directive_type=DirectiveType.NO_OP,
            structured_adjustment={},
            explanation="Baseline run"
        )
    ]
    response = solve_energy_schedule(scenario_101_request, directives)
    assert len(response.hourly_plan) == 24
    assert response.total_grid_kwh > 0
    assert response.total_cost_bdt > 0

    # Must pass replay validator without exceptions
    replay_and_validate_schedule(scenario_101_request, response, directives)

    # End-of-day battery neutrality
    assert abs(response.hourly_plan[23].battery_energy_after_kwh - scenario_101_request.battery.initial_energy_kwh) < 0.02


def test_optimizer_with_directives(scenario_101_request: OptimizeRequest):
    """Solve with solar reduction, battery reserve, and distractor."""
    directives = [
        DirectiveInterpretation(
            note_index=0,
            applies=True,
            directive_type=DirectiveType.SOLAR_REDUCTION,
            structured_adjustment={"hours": [11, 12, 13, 14], "factor": 0.3},
            explanation="Solar reduction"
        ),
        DirectiveInterpretation(
            note_index=1,
            applies=True,
            directive_type=DirectiveType.MINIMUM_BATTERY_RESERVE,
            structured_adjustment={"hours": [18, 19, 20, 21], "min_reserve_kwh": 35.0},
            explanation="Battery reserve"
        ),
        DirectiveInterpretation(
            note_index=2,
            applies=False,
            directive_type=DirectiveType.NO_OP,
            structured_adjustment={},
            explanation="Distractor"
        ),
    ]

    response = solve_energy_schedule(scenario_101_request, directives)

    # Replay validator check
    replay_and_validate_schedule(scenario_101_request, response, directives)

    # Verify battery reserve respected during affected hours
    for h in [18, 19, 20, 21]:
        assert response.hourly_plan[h].battery_energy_after_kwh >= 35.0 - 0.02


def test_validator_catches_energy_balance_violation(scenario_101_request: OptimizeRequest):
    """Tampering with grid draw must trigger a ScheduleConsistencyError."""
    directives = []
    response = solve_energy_schedule(scenario_101_request, directives)

    # Tamper with hour 5 grid draw
    response.hourly_plan[5].grid_kwh += 10.0

    with pytest.raises(ScheduleConsistencyError) as exc_info:
        replay_and_validate_schedule(scenario_101_request, response, directives)
    assert "Energy balance violation" in str(exc_info.value)


def test_validator_catches_neutrality_violation(scenario_101_request: OptimizeRequest):
    """End-of-day battery neutrality violation must trigger ScheduleConsistencyError."""
    directives = []
    response = solve_energy_schedule(scenario_101_request, directives)

    # Tamper with hour 23 battery energy
    response.hourly_plan[23].battery_energy_after_kwh += 5.0

    with pytest.raises(ScheduleConsistencyError) as exc_info:
        replay_and_validate_schedule(scenario_101_request, response, directives)
    assert "neutrality violated" in str(exc_info.value)
