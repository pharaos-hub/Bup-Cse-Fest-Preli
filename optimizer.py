"""
Linear Programming (LP) 24-Hour Energy Optimizer for GridWise.
Solves the schedule minimizing total grid cost subject to campus and battery constraints.
"""

import math
from typing import Dict, List, Tuple
from models import (
    BatteryAction,
    BatterySpecs,
    DirectiveInterpretation,
    DirectiveType,
    HourlyInput,
    HourlyPlan,
    OptimizeRequest,
    OptimizeResponse,
)


class OptimizationInfeasibleError(Exception):
    """Raised when the optimization problem has no feasible solution under given constraints."""
    pass


def _prepare_effective_bounds(
    hours: List[HourlyInput],
    battery: BatterySpecs,
    directives: List[DirectiveInterpretation],
) -> Tuple[List[float], List[float], List[float], List[float], List[float]]:
    """
    Computes hourly effective limits:
    - effective_solar[h]
    - min_battery_reserve[h]
    - max_charge[h]
    - max_discharge[h]
    - max_grid[h]
    """
    effective_solar = [h.solar_kwh for h in hours]
    min_reserve = [battery.minimum_energy_kwh for _ in range(24)]
    max_charge = [battery.max_charge_kwh_per_hour for _ in range(24)]
    max_discharge = [battery.max_discharge_kwh_per_hour for _ in range(24)]
    max_grid = [float("inf") for _ in range(24)]

    for d in directives:
        if not d.applies:
            continue
        adj = d.structured_adjustment or {}
        d_hours = adj.get("hours", [])
        raw_type = d.directive_type.value if isinstance(d.directive_type, DirectiveType) else str(d.directive_type)

        if raw_type == DirectiveType.SOLAR_REDUCTION.value:
            factor = float(adj.get("factor", 1.0))
            for h in d_hours:
                if 0 <= h < 24:
                    effective_solar[h] = effective_solar[h] * factor

        elif raw_type == DirectiveType.MINIMUM_BATTERY_RESERVE.value:
            res_val = float(adj.get("min_reserve_kwh", battery.minimum_energy_kwh))
            for h in d_hours:
                if 0 <= h < 24:
                    min_reserve[h] = max(min_reserve[h], res_val)

        elif raw_type == DirectiveType.NO_CHARGE_WINDOW.value:
            for h in d_hours:
                if 0 <= h < 24:
                    max_charge[h] = 0.0

        elif raw_type == DirectiveType.NO_DISCHARGE_WINDOW.value:
            for h in d_hours:
                if 0 <= h < 24:
                    max_discharge[h] = 0.0

        elif raw_type == DirectiveType.MAX_GRID_WINDOW.value:
            grid_cap = float(adj.get("max_grid_kwh", float("inf")))
            for h in d_hours:
                if 0 <= h < 24:
                    max_grid[h] = min(max_grid[h], grid_cap)

    return effective_solar, min_reserve, max_charge, max_discharge, max_grid


def _solve_with_pulp(
    hours: List[HourlyInput],
    battery: BatterySpecs,
    effective_solar: List[float],
    min_reserve: List[float],
    max_charge: List[float],
    max_discharge: List[float],
    max_grid: List[float],
) -> Tuple[List[float], List[float], List[float], List[float], List[float]]:
    import pulp

    prob = pulp.LpProblem("GridWise_Energy_Scheduling", pulp.LpMinimize)

    # Variables for each hour h in 0..23
    grid = [pulp.LpVariable(f"grid_{h}", lowBound=0.0, upBound=None if math.isinf(max_grid[h]) else max_grid[h]) for h in range(24)]
    solar_used = [pulp.LpVariable(f"solar_used_{h}", lowBound=0.0, upBound=effective_solar[h]) for h in range(24)]
    charge = [pulp.LpVariable(f"charge_{h}", lowBound=0.0, upBound=max_charge[h]) for h in range(24)]
    discharge = [pulp.LpVariable(f"discharge_{h}", lowBound=0.0, upBound=max_discharge[h]) for h in range(24)]
    e_after = [pulp.LpVariable(f"e_after_{h}", lowBound=min_reserve[h], upBound=battery.capacity_kwh) for h in range(24)]

    # Objective: Minimize cost with small tie-breakers
    # - prefer solar (- 1e-5)
    # - penalize redundant cycling (+ 1e-6 charge)
    prob += pulp.lpSum([
        grid[h] * hours[h].tariff_bdt_per_kwh - 1e-5 * solar_used[h] + 1e-6 * charge[h]
        for h in range(24)
    ])

    # Constraints
    for h in range(24):
        # 1. Energy balance: grid + solar_used + discharge = demand + charge
        prob += (grid[h] + solar_used[h] + discharge[h] == hours[h].demand_kwh + charge[h]), f"Balance_{h}"

        # 2. Battery energy equation: E_after[h] = E_prev + charge[h] - discharge[h]
        if h == 0:
            prob += (e_after[0] == battery.initial_energy_kwh + charge[0] - discharge[0]), "BatteryState_0"
        else:
            prob += (e_after[h] == e_after[h - 1] + charge[h] - discharge[h]), f"BatteryState_{h}"

    # 3. End-of-day neutrality: E_after[23] == initial_energy_kwh
    prob += (e_after[23] == battery.initial_energy_kwh), "Neutrality_23"

    # Solve quietly
    solver = pulp.PULP_CBC_CMD(msg=False)
    status = prob.solve(solver)

    if pulp.LpStatus[status] != "Optimal":
        raise OptimizationInfeasibleError(f"PuLP solver failed to find optimal schedule: status is {pulp.LpStatus[status]}")

    g_vals = [float(pulp.value(grid[h])) for h in range(24)]
    s_vals = [float(pulp.value(solar_used[h])) for h in range(24)]
    c_vals = [float(pulp.value(charge[h])) for h in range(24)]
    d_vals = [float(pulp.value(discharge[h])) for h in range(24)]
    e_vals = [float(pulp.value(e_after[h])) for h in range(24)]

    return g_vals, s_vals, c_vals, d_vals, e_vals


def _solve_with_scipy(
    hours: List[HourlyInput],
    battery: BatterySpecs,
    effective_solar: List[float],
    min_reserve: List[float],
    max_charge: List[float],
    max_discharge: List[float],
    max_grid: List[float],
) -> Tuple[List[float], List[float], List[float], List[float], List[float]]:
    from scipy.optimize import linprog
    import numpy as np

    # Decision variables layout: 5 * 24 = 120 variables
    # Order: [grid (0..23), solar (0..23), charge (0..23), discharge (0..23), e_after (0..23)]
    c = np.zeros(120)
    for h in range(24):
        c[h] = hours[h].tariff_bdt_per_kwh
        c[24 + h] = -1e-5
        c[48 + h] = 1e-6
        c[72 + h] = 0.0
        c[96 + h] = 0.0

    # Bounds
    bounds = []
    # Grid: 0 <= G_h <= max_grid[h]
    for h in range(24):
        ub = None if math.isinf(max_grid[h]) else max_grid[h]
        bounds.append((0.0, ub))
    # Solar: 0 <= S_h <= effective_solar[h]
    for h in range(24):
        bounds.append((0.0, effective_solar[h]))
    # Charge: 0 <= C_h <= max_charge[h]
    for h in range(24):
        bounds.append((0.0, max_charge[h]))
    # Discharge: 0 <= D_h <= max_discharge[h]
    for h in range(24):
        bounds.append((0.0, max_discharge[h]))
    # E_after: min_reserve[h] <= E_h <= capacity
    for h in range(24):
        bounds.append((min_reserve[h], battery.capacity_kwh))

    # Equality constraints:
    # 1. Energy balance (24 equations): G_h + S_h + D_h - C_h = demand_h
    # 2. Battery state (24 equations): E_h - C_h + D_h - E_{h-1} = 0 (or = E_init for h=0)
    # 3. End of day neutrality (1 equation): E_23 = E_init
    # Total equalities: 49
    A_eq = np.zeros((49, 120))
    b_eq = np.zeros(49)

    # 1. Energy balance
    for h in range(24):
        row = h
        A_eq[row, h] = 1.0        # G_h
        A_eq[row, 24 + h] = 1.0   # S_h
        A_eq[row, 72 + h] = 1.0   # D_h
        A_eq[row, 48 + h] = -1.0  # -C_h
        b_eq[row] = hours[h].demand_kwh

    # 2. Battery state
    for h in range(24):
        row = 24 + h
        A_eq[row, 96 + h] = 1.0   # E_h
        A_eq[row, 48 + h] = -1.0  # -C_h
        A_eq[row, 72 + h] = 1.0   # +D_h
        if h == 0:
            b_eq[row] = battery.initial_energy_kwh
        else:
            A_eq[row, 96 + (h - 1)] = -1.0  # -E_{h-1}
            b_eq[row] = 0.0

    # 3. Day neutrality
    A_eq[48, 96 + 23] = 1.0  # E_23
    b_eq[48] = battery.initial_energy_kwh

    res = linprog(c, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if not res.success:
        raise OptimizationInfeasibleError(f"SciPy linprog failed: {res.message}")

    x = res.x
    g_vals = x[0:24].tolist()
    s_vals = x[24:48].tolist()
    c_vals = x[48:72].tolist()
    d_vals = x[72:96].tolist()
    e_vals = x[96:120].tolist()

    return g_vals, s_vals, c_vals, d_vals, e_vals


def solve_energy_schedule(
    request: OptimizeRequest,
    sanitized_directives: List[DirectiveInterpretation],
) -> OptimizeResponse:
    """
    Builds and executes the 24-hour LP model, formatting the resulting plan.
    """
    effective_solar, min_reserve, max_charge, max_discharge, max_grid = _prepare_effective_bounds(
        request.hours, request.battery, sanitized_directives
    )

    try:
        g_vals, s_vals, c_vals, d_vals, e_vals = _solve_with_pulp(
            request.hours, request.battery, effective_solar, min_reserve, max_charge, max_discharge, max_grid
        )
    except Exception:
        # Fallback to SciPy linprog if PuLP encounters any solver binary or environment problem
        g_vals, s_vals, c_vals, d_vals, e_vals = _solve_with_scipy(
            request.hours, request.battery, effective_solar, min_reserve, max_charge, max_discharge, max_grid
        )

    hourly_plan: List[HourlyPlan] = []
    total_grid_kwh = 0.0
    total_cost_bdt = 0.0
    peak_grid_kwh = 0.0

    for h in range(24):
        g = max(0.0, g_vals[h])
        s = max(0.0, s_vals[h])
        c = max(0.0, c_vals[h])
        d = max(0.0, d_vals[h])
        e = e_vals[h]

        # Determine battery action and net transfer
        if c > 0.005 and d <= 0.005:
            action = BatteryAction.CHARGE
            batt_kwh = c
        elif d > 0.005 and c <= 0.005:
            action = BatteryAction.DISCHARGE
            batt_kwh = d
        elif c > 0.005 and d > 0.005:
            # Net simultaneous transfer if degenerate
            if c > d:
                action = BatteryAction.CHARGE
                batt_kwh = c - d
            else:
                action = BatteryAction.DISCHARGE
                batt_kwh = d - c
        else:
            action = BatteryAction.IDLE
            batt_kwh = 0.0

        hourly_cost = g * request.hours[h].tariff_bdt_per_kwh
        total_grid_kwh += g
        total_cost_bdt += hourly_cost
        if g > peak_grid_kwh:
            peak_grid_kwh = g

        hourly_plan.append(
            HourlyPlan(
                hour=h,
                grid_kwh=round(g, 2),
                solar_used_kwh=round(s, 2),
                battery_action=action,
                battery_kwh=round(batt_kwh, 2),
                battery_energy_after_kwh=round(e, 2),
            )
        )

    total_solar_avail = sum(effective_solar)
    total_solar_used = sum(p.solar_used_kwh for p in hourly_plan)
    solar_pct = (total_solar_used / total_solar_avail * 100.0) if total_solar_avail > 0 else 0.0

    applied_count = sum(1 for d in sanitized_directives if d.applies)
    plan_summary = (
        f"Scheduled 24h energy plan for scenario {request.scenario_id}: "
        f"{applied_count} active operator directives respected. "
        f"Total grid draw: {round(total_grid_kwh, 2)} kWh (Peak: {round(peak_grid_kwh, 2)} kWh) "
        f"costing {round(total_cost_bdt, 2)} BDT. "
        f"Solar utilization: {round(solar_pct, 1)}% ({round(total_solar_used, 2)} / {round(total_solar_avail, 2)} kWh). "
        f"End-of-day battery neutrality preserved at {round(request.battery.initial_energy_kwh, 2)} kWh."
    )

    return OptimizeResponse(
        scenario_id=request.scenario_id,
        directive_interpretation=sanitized_directives,
        hourly_plan=hourly_plan,
        total_grid_kwh=round(total_grid_kwh, 2),
        total_cost_bdt=round(total_cost_bdt, 2),
        peak_grid_kwh=round(peak_grid_kwh, 2),
        plan_summary=plan_summary,
    )
