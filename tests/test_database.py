"""
Integration tests for MongoDB Atlas database connectivity and operations.
"""

import pytest
from database import check_db_health, get_optimization_by_id, get_recent_optimizations, save_optimization


@pytest.mark.anyio
async def test_mongodb_connection_health():
    """Verify that MongoDB Atlas is reachable and ping returns ok."""
    status = await check_db_health()
    assert status.get("connected") is True
    assert status.get("database") == "gridwise_db"
    assert status.get("latency_ms") > 0


@pytest.mark.anyio
async def test_save_and_retrieve_run():
    """Verify storing an optimization run and retrieving it from MongoDB."""
    req = {
        "scenario_id": "TEST-MONGO-01",
        "operator_notes": ["Test note"],
        "hours": [],
        "battery": {}
    }
    resp = {
        "scenario_id": "TEST-MONGO-01",
        "directive_interpretation": [],
        "hourly_plan": [],
        "total_grid_kwh": 100.0,
        "total_cost_bdt": 1200.0,
        "peak_grid_kwh": 20.0,
        "plan_summary": "Test run summary"
    }

    doc_id = await save_optimization("TEST-MONGO-01", req, resp)
    assert doc_id is not None

    # Retrieve by ID
    doc = await get_optimization_by_id(doc_id)
    assert doc is not None
    assert doc["scenario_id"] == "TEST-MONGO-01"
    assert doc["summary"]["total_cost_bdt"] == 1200.0

    # Retrieve via history list
    history = await get_recent_optimizations(limit=5)
    assert any(h["id"] == doc_id for h in history)
