"""
GridWise Energy Optimization HTTP API Service.
BUP CSE Fest 2026 Hackathon Preliminary Round.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from database import check_db_health, get_optimization_by_id, get_recent_optimizations, save_optimization
from guardrails import validate_all_interpretations
from interpreter import interpret_all_notes
from models import OptimizeRequest, OptimizeResponse
from optimizer import OptimizationInfeasibleError, solve_energy_schedule
from validator import ScheduleConsistencyError, replay_and_validate_schedule

# Configure logging without leaking sensitive payloads
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("gridwise")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Verifies external services (like MongoDB Atlas) on startup."""
    logger.info("Starting GridWise Energy Scheduler...")
    db_status = await check_db_health()
    if db_status.get("connected"):
        logger.info(
            "MongoDB Atlas connected successfully to database '%s' (Ping: %.1f ms)",
            db_status.get("database"),
            db_status.get("latency_ms", 0.0),
        )
    else:
        logger.warning("MongoDB Atlas connection not available: %s", db_status.get("error"))
    yield
    logger.info("Shutting down GridWise Energy Scheduler...")


app = FastAPI(
    title="GridWise Energy Scheduler",
    description="LLM-assisted operator note interpretation and LP-based 24-hour campus energy scheduling.",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for external frontends (e.g. port 3000, 5173, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).parent / "frontend"



@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Returns HTTP 400 for malformed JSON or structurally invalid input schema.
    """
    errors = []
    for err in exc.errors():
        loc = " -> ".join(str(l) for l in err.get("loc", []))
        msg = err.get("msg", "Invalid value")
        errors.append(f"{loc}: {msg}")
    logger.warning("Request schema validation failed: %s", errors)
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "error": "Malformed or structurally invalid input",
            "details": errors,
        },
    )


@app.exception_handler(OptimizationInfeasibleError)
async def infeasible_exception_handler(request: Request, exc: OptimizationInfeasibleError):
    """
    Returns HTTP 422 for semantically well-formed scenarios that cannot be solved feasibly.
    """
    logger.error("Optimization infeasible: %s", str(exc))
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "Optimization infeasible",
            "detail": "Given battery constraints and directives preclude a feasible 24h schedule.",
        },
    )


@app.exception_handler(ScheduleConsistencyError)
async def consistency_exception_handler(request: Request, exc: ScheduleConsistencyError):
    """
    Returns controlled HTTP 500 when post-optimization safety replay fails.
    """
    logger.critical("Final schedule consistency failure: %s", str(exc))
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "Schedule consistency verification failed",
            "detail": "Generated energy schedule failed internal safety and balance replay.",
        },
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """
    Controlled internal server error handler that never leaks stack traces or credentials.
    """
    logger.error("Unhandled internal error: %s", str(exc), exc_info=False)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "Internal server error",
            "detail": "An unexpected error occurred while processing the energy schedule.",
        },
    )


@app.get("/health", status_code=status.HTTP_200_OK)
async def health_check() -> Dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


@app.post("/optimize-energy", response_model=OptimizeResponse, status_code=status.HTTP_200_OK)
async def optimize_energy(request: OptimizeRequest) -> OptimizeResponse:
    """
    Executes the 4-step energy scheduling pipeline:
    1. LLM Interpreter: Parse operator notes into structured directives.
    2. Guardrail Validator: Deterministically sanitize and validate directives.
    3. Math Optimizer: Solve the 24-hour linear program.
    4. Final Validator: Replay schedule to verify physical and logical consistency.
    """
    logger.info("Processing scenario_id: %s with %d notes", request.scenario_id, len(request.operator_notes))

    # Step 1: LLM Interpreter
    raw_directives = interpret_all_notes(request.operator_notes)

    # Step 2: Guardrail Validator
    sanitized_directives = validate_all_interpretations(
        raw_directives, num_notes=len(request.operator_notes), fail_safe=True
    )

    # Step 3: Math Optimizer
    response = solve_energy_schedule(request, sanitized_directives)

    # Step 4: Final Validator
    replay_and_validate_schedule(request, response, sanitized_directives)

    logger.info(
        "Successfully optimized scenario_id: %s | Total Cost: %.2f BDT | Total Grid: %.2f kWh",
        request.scenario_id,
        response.total_cost_bdt,
        response.total_grid_kwh,
    )

    # Persist scenario run to MongoDB Atlas asynchronously
    try:
        inserted_id = await save_optimization(
            scenario_id=request.scenario_id,
            request_data=request.model_dump(),
            response_data=response.model_dump(),
        )
        if inserted_id:
            logger.info("Persisted scenario %s with Mongo ID: %s", request.scenario_id, inserted_id)
    except Exception as db_err:
        logger.warning("Failed to persist run in MongoDB: %s", db_err)

    return response


@app.get("/db-health", status_code=status.HTTP_200_OK)
async def get_db_health() -> Dict[str, Any]:
    """Returns MongoDB Atlas connectivity status and collection statistics."""
    return await check_db_health()


@app.get("/history", status_code=status.HTTP_200_OK)
async def get_optimization_history(limit: int = 20) -> List[Dict[str, Any]]:
    """Returns past optimization runs stored in MongoDB."""
    return await get_recent_optimizations(limit=limit)


@app.get("/history/{run_id}", status_code=status.HTTP_200_OK)
async def get_run_details(run_id: str) -> Dict[str, Any]:
    """Fetches full request and response for a specific optimization run from MongoDB."""
    doc = await get_optimization_by_id(run_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found in database")
    return doc



# Mount frontend static assets and serve dashboard
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    @app.get("/dashboard", include_in_schema=False)
    async def serve_dashboard():
        return FileResponse(str(FRONTEND_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

