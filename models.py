"""
Pydantic data models for GridWise energy optimization service.
"""

from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


class DirectiveType(str, Enum):
    SOLAR_REDUCTION = "solar_reduction"
    MINIMUM_BATTERY_RESERVE = "minimum_battery_reserve"
    NO_CHARGE_WINDOW = "no_charge_window"
    NO_DISCHARGE_WINDOW = "no_discharge_window"
    MAX_GRID_WINDOW = "max_grid_window"
    NO_OP = "no_op"


class BatteryAction(str, Enum):
    CHARGE = "charge"
    DISCHARGE = "discharge"
    IDLE = "idle"


class HourlyInput(BaseModel):
    hour: int = Field(..., ge=0, le=23, description="Hour of the day (0-23)")
    demand_kwh: float = Field(..., ge=0.0, description="Demand in kWh")
    solar_kwh: float = Field(..., ge=0.0, description="Available solar generation in kWh")
    tariff_bdt_per_kwh: float = Field(..., ge=0.0, description="Tariff in BDT per kWh")


class BatterySpecs(BaseModel):
    capacity_kwh: float = Field(..., gt=0.0, description="Total battery capacity in kWh")
    initial_energy_kwh: float = Field(..., ge=0.0, description="Energy in battery at start of hour 0 in kWh")
    minimum_energy_kwh: float = Field(..., ge=0.0, description="Minimum reserve energy in kWh")
    max_charge_kwh_per_hour: float = Field(..., ge=0.0, description="Maximum charge rate in kWh/hour")
    max_discharge_kwh_per_hour: float = Field(..., ge=0.0, description="Maximum discharge rate in kWh/hour")

    @model_validator(mode="after")
    def validate_battery_bounds(self) -> "BatterySpecs":
        if self.minimum_energy_kwh > self.capacity_kwh:
            raise ValueError("minimum_energy_kwh cannot exceed capacity_kwh")
        if self.initial_energy_kwh > self.capacity_kwh:
            raise ValueError("initial_energy_kwh cannot exceed capacity_kwh")
        if self.initial_energy_kwh < self.minimum_energy_kwh:
            raise ValueError("initial_energy_kwh cannot be less than minimum_energy_kwh")
        return self


class OptimizeRequest(BaseModel):
    scenario_id: str = Field(..., min_length=1, description="Unique scenario identifier")
    operator_notes: List[str] = Field(..., min_length=1, max_length=3, description="1 to 3 operator notes")
    hours: List[HourlyInput] = Field(..., min_length=24, max_length=24, description="Exactly 24 hourly records")
    battery: BatterySpecs = Field(..., description="Battery configuration parameters")

    @field_validator("hours")
    @classmethod
    def validate_24_hours(cls, v: List[HourlyInput]) -> List[HourlyInput]:
        if len(v) != 24:
            raise ValueError("Exactly 24 hours must be provided")
        hour_indices = [h.hour for h in v]
        if hour_indices != list(range(24)):
            raise ValueError(f"Hours must be strictly ordered from 0 to 23, got: {hour_indices}")
        return v


class StructuredAdjustment(BaseModel):
    hours: Optional[List[int]] = Field(default=None, description="Affected hours (0-23, ascending)")
    factor: Optional[float] = Field(default=None, description="Solar reduction remaining fraction (0.0 to 1.0)")
    min_reserve_kwh: Optional[float] = Field(default=None, ge=0.0, description="Minimum battery reserve in kWh")
    max_grid_kwh: Optional[float] = Field(default=None, ge=0.0, description="Max grid cap in kWh")


class DirectiveInterpretation(BaseModel):
    note_index: int = Field(..., ge=0, description="0-based index of corresponding operator note")
    applies: bool = Field(..., description="Whether this note impacts the energy schedule")
    directive_type: DirectiveType = Field(..., description="Classification of the directive")
    structured_adjustment: Dict[str, Any] = Field(default_factory=dict, description="Extracted parameters")
    explanation: str = Field(..., description="Reasoning for this interpretation")


class HourlyPlan(BaseModel):
    hour: int = Field(..., ge=0, le=23, description="Hour of the day")
    grid_kwh: float = Field(..., description="Energy drawn from the grid in kWh")
    solar_used_kwh: float = Field(..., description="Solar energy directly consumed or stored in kWh")
    battery_action: BatteryAction = Field(..., description="Battery state: charge, discharge, or idle")
    battery_kwh: float = Field(..., description="Energy transferred into or out of battery in kWh")
    battery_energy_after_kwh: float = Field(..., description="State of energy in battery at end of hour in kWh")


class OptimizeResponse(BaseModel):
    scenario_id: str = Field(..., description="Scenario identifier")
    directive_interpretation: List[DirectiveInterpretation] = Field(..., description="Interpreted directives")
    hourly_plan: List[HourlyPlan] = Field(..., min_length=24, max_length=24, description="Optimized 24-hour schedule")
    total_grid_kwh: float = Field(..., description="Total grid energy imported across 24h in kWh")
    total_cost_bdt: float = Field(..., description="Total energy cost in BDT")
    peak_grid_kwh: float = Field(..., description="Maximum grid draw in any single hour in kWh")
    plan_summary: str = Field(..., description="Executive summary of the generated schedule")
