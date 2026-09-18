# GridWise Energy Optimization Service

**GridWise** is a production-ready HTTP API service built for the **BUP CSE Fest 2026 Hackathon Preliminary Round**. It provides LLM-assisted interpretation of natural language operator notes combined with an LP/MILP solver for optimal 24-hour smart campus energy scheduling (grid + solar + battery storage).

---

## Architecture Pipeline

GridWise enforces a strict, four-stage sequential pipeline:

```mermaid
flowchart LR
    A["Operator Notes"] --> B["1. LLM Interpreter"]
    B --> C["2. Guardrail Validator"]
    C --> D["3. LP Math Optimizer"]
    D --> E["4. Final Replay Validator"]
    E --> F["Optimized 24h Plan JSON"]
```

1. **LLM Interpreter (`interpreter.py`)**: Parses natural language operator notes into structured JSON conforming strictly to one of six directive types (`solar_reduction`, `minimum_battery_reserve`, `no_charge_window`, `no_discharge_window`, `max_grid_window`, `no_op`). Features both OpenAI/Anthropic structured outputs and a deterministic offline rule engine.
2. **Guardrail Validator (`guardrails.py`)**: Pure deterministic validation. Rejects/sanitizes unsupported types, mismatched applies flags, invalid/non-ascending hours, negative reserves or grid caps, and out-of-bound factors. Malformed directives are safely neutralized without leaking stack traces.
3. **Math Optimizer (`optimizer.py`)**: Formulates a 24-hour Linear Program (LP) across decision variables ($G_h, S_h, C_h, D_h, E_h$) to minimize total grid import costs while preserving energy balance, battery capacity, rate limits, and end-of-day battery neutrality ($E_{23} = E_{init}$).
4. **Final Replay Validator (`validator.py`)**: Replays the solved schedule hour-by-hour against physical and operational constraints to guarantee 100% internal consistency before responding.

---

## Core Correctness Rules

- **Half-Open Windows**: Time intervals are strictly half-open $[start, end)$ — e.g., "1 PM to 3 PM" corresponds to hours `[13, 14]`.
- **Solar Reduction Factor**: `factor` is the fraction of solar power *remaining* (e.g. 70% reduction $\rightarrow$ `factor = 0.30`).
- **Distractor Notes**: Notes unrelated to energy dispatch resolve to `directive_type: "no_op"` with `applies: false`.
- **Battery Neutrality**: Battery energy at the end of the day ($h=23$) equals initial battery energy ($E_{init}$).
- **Tolerance**: Numeric balance and boundary tolerance within $\pm 0.01$ kWh / $\pm 0.01$ BDT.

---

## Environment Configuration

Create a `.env` file or export the following environment variables (optional — offline fallback is enabled by default):

```bash
# LLM Provider: 'openai' (default) or 'anthropic'
LLM_PROVIDER=openai

# OpenAI settings
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# Anthropic settings
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-3-5-haiku-20241022
```

---

## Local Setup & Run

### 1. Prerequisites
- Python 3.10+ (Python 3.12 recommended)
- `pip`

### 2. Installation
```bash
# Clone or navigate to the project directory
cd gridwise

# Create and activate a virtual environment
python -m venv .venv
# On Linux/macOS:
source .venv/bin/activate
# On Windows (PowerShell):
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

### 3. Start the Service
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The API will be live at `http://localhost:8000`. Interactive API docs (Swagger) are available at `http://localhost:8000/docs`.

---

## Running with Docker

```bash
# Build the Docker image
docker build -t gridwise:latest .

# Run the container
docker run -p 8000:8000 --env-file .env gridwise:latest
```

---

## API Endpoints & Curl Examples

### 1. Health Check
```bash
curl -X GET http://localhost:8000/health
```

**Response (200 OK):**
```json
{
  "status": "ok"
}
```

### 2. Energy Optimization (`/optimize-energy`)

```bash
curl -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d '{
    "scenario_id": "GRID-101",
    "operator_notes": [
      "Severe dust storm forecast from 11 AM to 3 PM; expect solar output to drop by 70%.",
      "Keep battery reserve at minimum 35 kWh between 6 PM and 10 PM for campus evening event.",
      "Shift log: Maintenance staff completed routine inspection on inverter inverter-B at 10 AM."
    ],
    "hours": [
      {"hour": 0, "demand_kwh": 25.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 6.5},
      {"hour": 1, "demand_kwh": 22.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 6.5},
      {"hour": 2, "demand_kwh": 20.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 6.5},
      {"hour": 3, "demand_kwh": 20.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 6.5},
      {"hour": 4, "demand_kwh": 22.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 6.5},
      {"hour": 5, "demand_kwh": 28.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 6.5},
      {"hour": 6, "demand_kwh": 35.0, "solar_kwh": 5.0, "tariff_bdt_per_kwh": 8.0},
      {"hour": 7, "demand_kwh": 50.0, "solar_kwh": 20.0, "tariff_bdt_per_kwh": 8.0},
      {"hour": 8, "demand_kwh": 70.0, "solar_kwh": 45.0, "tariff_bdt_per_kwh": 10.5},
      {"hour": 9, "demand_kwh": 85.0, "solar_kwh": 70.0, "tariff_bdt_per_kwh": 10.5},
      {"hour": 10, "demand_kwh": 95.0, "solar_kwh": 90.0, "tariff_bdt_per_kwh": 10.5},
      {"hour": 11, "demand_kwh": 100.0, "solar_kwh": 100.0, "tariff_bdt_per_kwh": 10.5},
      {"hour": 12, "demand_kwh": 105.0, "solar_kwh": 105.0, "tariff_bdt_per_kwh": 10.5},
      {"hour": 13, "demand_kwh": 100.0, "solar_kwh": 95.0, "tariff_bdt_per_kwh": 10.5},
      {"hour": 14, "demand_kwh": 95.0, "solar_kwh": 85.0, "tariff_bdt_per_kwh": 10.5},
      {"hour": 15, "demand_kwh": 85.0, "solar_kwh": 65.0, "tariff_bdt_per_kwh": 10.5},
      {"hour": 16, "demand_kwh": 75.0, "solar_kwh": 35.0, "tariff_bdt_per_kwh": 10.5},
      {"hour": 17, "demand_kwh": 80.0, "solar_kwh": 10.0, "tariff_bdt_per_kwh": 14.0},
      {"hour": 18, "demand_kwh": 90.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 14.0},
      {"hour": 19, "demand_kwh": 85.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 14.0},
      {"hour": 20, "demand_kwh": 75.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 14.0},
      {"hour": 21, "demand_kwh": 60.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 10.5},
      {"hour": 22, "demand_kwh": 45.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 8.0},
      {"hour": 23, "demand_kwh": 30.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 6.5}
    ],
    "battery": {
      "capacity_kwh": 100.0,
      "initial_energy_kwh": 40.0,
      "minimum_energy_kwh": 20.0,
      "max_charge_kwh_per_hour": 25.0,
      "max_discharge_kwh_per_hour": 25.0
    }
  }'
```

**Response (200 OK):**
```json
{
  "scenario_id": "GRID-101",
  "directive_interpretation": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": {
        "hours": [11, 12, 13, 14],
        "factor": 0.3
      },
      "explanation": "Solar output adjusted to remaining factor of 0.3 during specified window."
    },
    {
      "note_index": 1,
      "applies": true,
      "directive_type": "minimum_battery_reserve",
      "structured_adjustment": {
        "hours": [18, 19, 20, 21],
        "min_reserve_kwh": 35.0
      },
      "explanation": "Elevated minimum battery reserve of 35.0 kWh enforced."
    },
    {
      "note_index": 2,
      "applies": false,
      "directive_type": "no_op",
      "structured_adjustment": {},
      "explanation": "Administrative or non-energy related note detected."
    }
  ],
  "hourly_plan": [
    {
      "hour": 0,
      "grid_kwh": 25.0,
      "solar_used_kwh": 0.0,
      "battery_action": "idle",
      "battery_kwh": 0.0,
      "battery_energy_after_kwh": 40.0
    },
    ...
  ],
  "total_grid_kwh": 1109.5,
  "total_cost_bdt": 12185.0,
  "peak_grid_kwh": 85.0,
  "plan_summary": "Scheduled 24h energy plan for scenario GRID-101: 2 active operator directives respected..."
}
```

---

## Running Automated Tests

Run the full pytest suite:

```bash
pytest -v
```
# Bup-Cse-Fest-Preli
