"""
Final Replay Validator for GridWise.
Replays the generated 24-hour schedule against all constraints, base rules,
and applied directives to guarantee internal physical and logical consistency.
"""

from typing import List
from models import (
    BatteryAction,
    DirectiveInterpretation,
    OptimizeRequest,
    OptimizeResponse,
)
from optimizer import _prepare_effective_bounds


class ScheduleConsistencyError(Exception):
    """Raised when the generated schedule violates physical or directive constraints upon replay."""
    pass


TOLERANCE_KWH = 0.03  # Accounts for 2-decimal place round-off


def replay_and_validate_schedule(
    request: OptimizeRequest,
    response: OptimizeResponse,
    sanitized_directives: List[DirectiveInterpretation],
) -> None:
    """
    Simulates the schedule hour-by-hour and verifies every balance, boundary,
    and directive condition. Raises ScheduleConsistencyError on failure.
    """
    effective_solar, min_reserve, max_charge, max_discharge, max_grid = _prepare_effective_bounds(
        request.hours, request.battery, sanitized_directives
    )

    # 0. End-of-day battery neutrality
    if abs(response.hourly_plan[23].battery_energy_after_kwh - request.battery.initial_energy_kwh) > TOLERANCE_KWH:
        raise ScheduleConsistencyError(
            f"End of day neutrality violated: Hour 23 energy {response.hourly_plan[23].battery_energy_after_kwh:.2f} "
            f"!= initial energy {request.battery.initial_energy_kwh:.2f}"
        )

    prev_energy = request.battery.initial_energy_kwh

    for h, plan in enumerate(response.hourly_plan):
        # 1. Non-negativity
        if plan.grid_kwh < -TOLERANCE_KWH:
            raise ScheduleConsistencyError(f"Hour {h}: Negative grid import ({plan.grid_kwh} kWh)")
        if plan.solar_used_kwh < -TOLERANCE_KWH:
            raise ScheduleConsistencyError(f"Hour {h}: Negative solar used ({plan.solar_used_kwh} kWh)")
        if plan.battery_kwh < -TOLERANCE_KWH:
            raise ScheduleConsistencyError(f"Hour {h}: Negative battery transfer ({plan.battery_kwh} kWh)")

        # 2. Solar bounds
        if plan.solar_used_kwh > effective_solar[h] + TOLERANCE_KWH:
            raise ScheduleConsistencyError(
                f"Hour {h}: Solar used ({plan.solar_used_kwh}) exceeds available effective solar ({effective_solar[h]})"
            )

        # 3. Grid limits (from max_grid_window)
        if plan.grid_kwh > max_grid[h] + TOLERANCE_KWH:
            raise ScheduleConsistencyError(
                f"Hour {h}: Grid import ({plan.grid_kwh}) exceeds max grid cap ({max_grid[h]})"
            )

        # 4. Battery actions and rate limits
        if plan.battery_action == BatteryAction.CHARGE:
            charge_kwh = plan.battery_kwh
            discharge_kwh = 0.0
            if charge_kwh > max_charge[h] + TOLERANCE_KWH:
                raise ScheduleConsistencyError(
                    f"Hour {h}: Battery charge ({charge_kwh}) exceeds allowed max charge ({max_charge[h]})"
                )
        elif plan.battery_action == BatteryAction.DISCHARGE:
            charge_kwh = 0.0
            discharge_kwh = plan.battery_kwh
            if discharge_kwh > max_discharge[h] + TOLERANCE_KWH:
                raise ScheduleConsistencyError(
                    f"Hour {h}: Battery discharge ({discharge_kwh}) exceeds allowed max discharge ({max_discharge[h]})"
                )
        else:
            charge_kwh = 0.0
            discharge_kwh = 0.0
            if plan.battery_kwh > TOLERANCE_KWH:
                raise ScheduleConsistencyError(
                    f"Hour {h}: Battery is IDLE but transfer magnitude is {plan.battery_kwh} kWh"
                )

        # 5. Energy balance: grid + solar_used + discharge = demand + charge
        supply = plan.grid_kwh + plan.solar_used_kwh + discharge_kwh
        demand = request.hours[h].demand_kwh + charge_kwh
        if abs(supply - demand) > TOLERANCE_KWH:
            raise ScheduleConsistencyError(
                f"Hour {h}: Energy balance violation. Supply={supply:.2f} kWh != Demand={demand:.2f} kWh (diff={supply-demand:.3f})"
            )

        # 6. Battery state update: E_after = E_prev + charge - discharge
        expected_energy = prev_energy + charge_kwh - discharge_kwh
        if abs(plan.battery_energy_after_kwh - expected_energy) > TOLERANCE_KWH:
            raise ScheduleConsistencyError(
                f"Hour {h}: Battery state tracking mismatch. Reported E_after={plan.battery_energy_after_kwh:.2f}, expected={expected_energy:.2f}"
            )

        # 7. Battery capacity & reserve limits
        if plan.battery_energy_after_kwh < min_reserve[h] - TOLERANCE_KWH:
            raise ScheduleConsistencyError(
                f"Hour {h}: Battery energy {plan.battery_energy_after_kwh:.2f} dropped below required reserve {min_reserve[h]:.2f}"
            )
        if plan.battery_energy_after_kwh > request.battery.capacity_kwh + TOLERANCE_KWH:
            raise ScheduleConsistencyError(
                f"Hour {h}: Battery energy {plan.battery_energy_after_kwh:.2f} exceeded capacity {request.battery.capacity_kwh:.2f}"
            )

        prev_energy = plan.battery_energy_after_kwh

    # 8. End-of-day battery neutrality
    if abs(response.hourly_plan[23].battery_energy_after_kwh - request.battery.initial_energy_kwh) > TOLERANCE_KWH:
        raise ScheduleConsistencyError(
            f"End of day neutrality violated: Hour 23 energy {response.hourly_plan[23].battery_energy_after_kwh:.2f} "
            f"!= initial energy {request.battery.initial_energy_kwh:.2f}"
        )
