"""
Integration tests for FastAPI endpoints: /health and /optimize-energy.
"""

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health_endpoint():
    """Verify GET /health returns 200 {'status': 'ok'}."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_optimize_energy_grid_101():
    """End-to-end test with scenario GRID-101."""
    fixture_path = Path(__file__).parent / "data" / "scenario_grid_101.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    response = client.post("/optimize-energy", json=payload)
    assert response.status_code == 200
    data = response.json()

    # Schema checks
    assert data["scenario_id"] == "GRID-101"
    assert len(data["directive_interpretation"]) == 3
    assert len(data["hourly_plan"]) == 24
    assert data["total_grid_kwh"] > 0
    assert data["total_cost_bdt"] > 0
    assert data["peak_grid_kwh"] > 0
    assert "plan_summary" in data and len(data["plan_summary"]) > 20

    # Directives checks
    d0 = data["directive_interpretation"][0]
    assert d0["note_index"] == 0
    assert d0["applies"] is True
    assert d0["directive_type"] == "solar_reduction"
    assert d0["structured_adjustment"]["hours"] == [11, 12, 13, 14]
    assert abs(d0["structured_adjustment"]["factor"] - 0.3) < 1e-4

    d1 = data["directive_interpretation"][1]
    assert d1["note_index"] == 1
    assert d1["applies"] is True
    assert d1["directive_type"] == "minimum_battery_reserve"
    assert d1["structured_adjustment"]["min_reserve_kwh"] == 35.0

    d2 = data["directive_interpretation"][2]
    assert d2["note_index"] == 2
    assert d2["applies"] is False
    assert d2["directive_type"] == "no_op"

    # Verify battery neutrality at hour 23
    assert abs(data["hourly_plan"][23]["battery_energy_after_kwh"] - payload["battery"]["initial_energy_kwh"]) < 0.05


def test_malformed_input_returns_400():
    """Verify malformed inputs return HTTP 400."""
    # Missing scenario_id and hours
    bad_payload = {"operator_notes": ["some note"]}
    response = client.post("/optimize-energy", json=bad_payload)
    assert response.status_code == 400
    err = response.json()
    assert "error" in err
    assert "details" in err


def test_invalid_hours_count_returns_400():
    """Verify providing only 5 hours instead of 24 returns HTTP 400."""
    fixture_path = Path(__file__).parent / "data" / "scenario_grid_101.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    # Slice to 5 hours
    payload["hours"] = payload["hours"][:5]
    response = client.post("/optimize-energy", json=payload)
    assert response.status_code == 400
    err = response.json()
    assert "hours" in str(err)
