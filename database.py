"""
MongoDB database integration module for GridWise using Motor (asyncio driver).
Stores scenario runs, optimization logs, and historical schedules.
"""

from datetime import datetime, timezone
import logging
import os
from typing import Any, Dict, List, Optional
from bson import ObjectId
from dotenv import load_dotenv
import motor.motor_asyncio

# Load .env file if present
load_dotenv()

logger = logging.getLogger("gridwise.db")

MONGODB_URI = os.getenv(
    "MONGODB_URI",
    "mongodb+srv://nusratfarihacs_db_user:PEfQZY8AWAYPBdl7@cluster0.sfedpco.mongodb.net/?retryWrites=true&w=majority"
)
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "gridwise_db")

client: Optional[motor.motor_asyncio.AsyncIOMotorClient] = None
db = None


import asyncio

_active_loop = None

def get_database():
    """Returns database handle, initializing client or rebinding to current event loop if needed."""
    global client, db, _active_loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if client is None or (current_loop is not None and current_loop != _active_loop):
        client = motor.motor_asyncio.AsyncIOMotorClient(
            MONGODB_URI,
            serverSelectionTimeoutMS=10000,
            connectTimeoutMS=10000
        )
        db = client[MONGODB_DB_NAME]
        _active_loop = current_loop

    return db


async def check_db_health() -> Dict[str, Any]:
    """Tests connection to MongoDB Atlas and returns status details."""
    try:
        database = get_database()
        start = datetime.now()
        await database.command("ping")
        latency_ms = round((datetime.now() - start).total_seconds() * 1000, 2)
        count = await database.optimizations.count_documents({})
        return {
            "connected": True,
            "database": MONGODB_DB_NAME,
            "latency_ms": latency_ms,
            "total_optimizations_stored": count
        }
    except Exception as e:
        logger.warning("MongoDB ping failed: %s", str(e))
        return {
            "connected": False,
            "database": MONGODB_DB_NAME,
            "error": str(e)
        }


async def save_optimization(
    scenario_id: str,
    request_data: Dict[str, Any],
    response_data: Dict[str, Any]
) -> Optional[str]:
    """
    Saves an optimization run to the 'optimizations' collection.
    Returns the string ID of the inserted document.
    """
    try:
        database = get_database()
        document = {
            "scenario_id": scenario_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "request": request_data,
            "response": response_data,
            "summary": {
                "total_cost_bdt": response_data.get("total_cost_bdt"),
                "total_grid_kwh": response_data.get("total_grid_kwh"),
                "peak_grid_kwh": response_data.get("peak_grid_kwh"),
                "directives_count": len(response_data.get("directive_interpretation", []))
            }
        }
        result = await database.optimizations.insert_one(document)
        logger.info("Saved optimization run for scenario %s to MongoDB with ID %s", scenario_id, result.inserted_id)
        return str(result.inserted_id)
    except Exception as e:
        logger.error("Failed to save optimization to MongoDB: %s", str(e))
        return None


async def get_recent_optimizations(limit: int = 20) -> List[Dict[str, Any]]:
    """Retrieves the most recent optimization runs."""
    try:
        database = get_database()
        cursor = database.optimizations.find().sort("created_at", -1).limit(limit)
        results = []
        async for doc in cursor:
            results.append({
                "id": str(doc["_id"]),
                "scenario_id": doc.get("scenario_id"),
                "created_at": doc.get("created_at"),
                "summary": doc.get("summary", {}),
                "plan_summary": doc.get("response", {}).get("plan_summary")
            })
        return results
    except Exception as e:
        logger.error("Failed to retrieve optimization history from MongoDB: %s", str(e))
        return []


async def get_optimization_by_id(run_id: str) -> Optional[Dict[str, Any]]:
    """Retrieves full details of a specific optimization run."""
    try:
        database = get_database()
        doc = await database.optimizations.find_one({"_id": ObjectId(run_id)})
        if doc:
            doc["id"] = str(doc.pop("_id"))
            return doc
        return None
    except Exception as e:
        logger.error("Failed to fetch run %s: %s", run_id, str(e))
        return None
