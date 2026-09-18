"""
LLM Interpreter module for GridWise.
Parses natural language operator notes into structured directive objects using
an LLM (OpenAI or Anthropic) or a deterministic fallback rule engine when offline.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

from models import DirectiveInterpretation, DirectiveType

SYSTEM_PROMPT = """You are the GridWise Operator Note Interpreter for a smart campus energy management system.
Your job is to analyze an operator note and extract exactly one structured energy directive.

There are exactly SIX directive types:
1. "solar_reduction": Solar generation is degraded or curtailed.
   - "factor": Float between 0.0 and 1.0 representing the FRACTION REMAINING (e.g., 80% reduction means factor is 0.20; reduced to 25% means factor is 0.25).
   - "hours": List of integer hours [0..23], strictly ascending, half-open interval [start, end) where end hour is excluded.
2. "minimum_battery_reserve": Battery must maintain an elevated reserve.
   - "min_reserve_kwh": Float >= 0.0 (in kWh).
   - "hours": List of integer hours [0..23], strictly ascending, half-open interval [start, end).
3. "no_charge_window": Battery charging is forbidden during this window.
   - "hours": List of integer hours [0..23], strictly ascending, half-open interval [start, end).
4. "no_discharge_window": Battery discharging is forbidden during this window.
   - "hours": List of integer hours [0..23], strictly ascending, half-open interval [start, end).
5. "max_grid_window": Grid import cannot exceed a specified cap.
   - "max_grid_kwh": Float >= 0.0 (in kWh).
   - "hours": List of integer hours [0..23], strictly ascending, half-open interval [start, end).
6. "no_op": The note is a distractor, administrative reminder, or does not affect energy dispatch.
   - "applies": false
   - "structured_adjustment": {}

CRITICAL RULES:
- Time windows are half-open: "1 PM to 3 PM" means hours [13, 14]. "8 AM to 11 AM" means hours [8, 9, 10].
- "solar_reduction.factor" is the fraction of solar power REMAINING (not lost).
- Distractor notes (e.g. shift logs, coffee breaks, general reminders) MUST have directive_type="no_op" and applies=false.
- If directive_type is "no_op", applies MUST be false. Otherwise applies MUST be true.
- Output MUST be valid JSON conforming strictly to the requested schema.

Output Schema:
{
  "note_index": <int>,
  "applies": <bool>,
  "directive_type": "solar_reduction" | "minimum_battery_reserve" | "no_charge_window" | "no_discharge_window" | "max_grid_window" | "no_op",
  "structured_adjustment": {
    "hours": [<int>, ...],
    "factor": <float>,
    "min_reserve_kwh": <float>,
    "max_grid_kwh": <float>
  },
  "explanation": "<brief rationale>"
}
"""


def parse_time_window(text: str) -> List[int]:
    """
    Parses natural language time spans into a list of half-open integer hours.
    Example: "1 PM to 3 PM" -> [13, 14]
    """
    # Look for "X AM/PM to Y AM/PM" or "between X:00 and Y:00"
    m = re.search(r"(\d{1,2})(?::\d{2})?\s*(am|pm)?\s*(?:to|until|-|and)\s*(\d{1,2})(?::\d{2})?\s*(am|pm)?", text, re.IGNORECASE)
    if m:
        h1 = int(m.group(1))
        mer1 = m.group(2)
        h2 = int(m.group(3))
        mer2 = m.group(4)

        if mer2 and not mer1:
            mer1 = mer2

        if mer1:
            if mer1.lower() == "pm" and h1 != 12:
                h1 += 12
            elif mer1.lower() == "am" and h1 == 12:
                h1 = 0

        if mer2:
            if mer2.lower() == "pm" and h2 != 12:
                h2 += 12
            elif mer2.lower() == "am" and h2 == 12:
                h2 = 0

        if 0 <= h1 < h2 <= 24:
            return list(range(h1, h2))

    # Look for 24h formats e.g. "hours 13 to 15" or "13:00 to 15:00"
    m2 = re.search(r"(?:hours?|from)?\s*(\d{1,2}):00\s*(?:to|-|until)\s*(\d{1,2}):00", text, re.IGNORECASE)
    if m2:
        h1 = int(m2.group(1))
        h2 = int(m2.group(2))
        if 0 <= h1 < h2 <= 24:
            return list(range(h1, h2))

    return []


def deterministic_fallback_interpret(note: str, note_index: int) -> DirectiveInterpretation:
    """
    Deterministic rule-based interpreter used when no LLM API key is present
    or as a safe backup parser.
    """
    low = note.lower()

    # Distractor check
    distractor_words = [
        "coffee", "meeting", "submit", "lunch", "supervisor", "shift log",
        "badge", "visitor", "routine inspection complete", "admin", "reminder to log"
    ]
    if any(w in low for w in distractor_words) and not any(
        k in low for k in ["solar", "battery", "grid", "charge", "discharge", "curtail"]
    ):
        return DirectiveInterpretation(
            note_index=note_index,
            applies=False,
            directive_type=DirectiveType.NO_OP,
            structured_adjustment={},
            explanation="Administrative or non-energy related note detected."
        )

    # 1. Solar reduction
    if any(k in low for k in ["solar", "cloud", "dust", "panel", "sun"]):
        hours = parse_time_window(note) or [11, 12, 13, 14]
        # Check percentage
        pct_match = re.search(r"(\d+(?:\.\d+)?)\s*%", note)
        factor = 0.5  # default
        if pct_match:
            pct = float(pct_match.group(1))
            if "reduc" in low or "drop" in low or "cut" in low or "loss" in low:
                factor = max(0.0, min(1.0, 1.0 - (pct / 100.0)))
            else:
                factor = max(0.0, min(1.0, pct / 100.0))
        elif "half" in low:
            factor = 0.5

        return DirectiveInterpretation(
            note_index=note_index,
            applies=True,
            directive_type=DirectiveType.SOLAR_REDUCTION,
            structured_adjustment={"hours": hours, "factor": round(factor, 4)},
            explanation=f"Solar output adjusted to remaining factor of {factor} during specified window."
        )

    # 2. Minimum battery reserve
    if "reserve" in low or ("keep battery" in low and ("at least" in low or "above" in low or "minimum" in low)):
        hours = parse_time_window(note) or [18, 19, 20, 21]
        val_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:kwh|%)?", low)
        reserve_val = float(val_match.group(1)) if val_match else 30.0
        return DirectiveInterpretation(
            note_index=note_index,
            applies=True,
            directive_type=DirectiveType.MINIMUM_BATTERY_RESERVE,
            structured_adjustment={"hours": hours, "min_reserve_kwh": reserve_val},
            explanation=f"Elevated minimum battery reserve of {reserve_val} kWh enforced."
        )

    # 3. No charge window
    no_charge_pattern = r"\b(?:no\s+(?:\w+\s+)?charg\w+|do\s+not\s+charg\w+|halt\s+charg\w+|stop\s+charg\w+|prohibit\s+charg\w+)\b"
    if re.search(no_charge_pattern, low) and not re.search(r"\bdischarg\w+", low):
        hours = parse_time_window(note) or [14, 15, 16]
        return DirectiveInterpretation(
            note_index=note_index,
            applies=True,
            directive_type=DirectiveType.NO_CHARGE_WINDOW,
            structured_adjustment={"hours": hours},
            explanation="Battery charging prohibited during specified window."
        )

    # 4. No discharge window
    no_discharge_pattern = r"\b(?:no\s+(?:\w+\s+)?discharg\w+|do\s+not\s+discharg\w+|halt\s+discharg\w+|stop\s+discharg\w+|prohibit\s+discharg\w+)\b"
    if re.search(no_discharge_pattern, low):
        hours = parse_time_window(note) or [8, 9, 10]
        return DirectiveInterpretation(
            note_index=note_index,
            applies=True,
            directive_type=DirectiveType.NO_DISCHARGE_WINDOW,
            structured_adjustment={"hours": hours},
            explanation="Battery discharging prohibited during specified window."
        )

    # 5. Max grid window
    if "grid" in low and ("cap" in low or "limit" in low or "max" in low or "ceiling" in low or "cannot exceed" in low):
        hours = parse_time_window(note) or [17, 18, 19]
        val_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:kwh|kw)", low)
        cap_val = float(val_match.group(1)) if val_match else 50.0
        return DirectiveInterpretation(
            note_index=note_index,
            applies=True,
            directive_type=DirectiveType.MAX_GRID_WINDOW,
            structured_adjustment={"hours": hours, "max_grid_kwh": cap_val},
            explanation=f"Grid import capped at {cap_val} kWh during specified window."
        )

    # Default to no_op
    return DirectiveInterpretation(
        note_index=note_index,
        applies=False,
        directive_type=DirectiveType.NO_OP,
        structured_adjustment={},
        explanation="No applicable energy directive recognized from note."
    )


def _extract_json_from_text(text: str) -> Dict[str, Any]:
    """Extracts JSON object from possible markdown code blocks or surrounding text."""
    # First try direct parse
    try:
        return json.loads(text.strip())
    except Exception:
        pass

    # Try markdown block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))

    # Try finding outermost braces
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])

    raise ValueError(f"Could not parse JSON from model output: {text}")


def call_openai_interpreter(note: str, note_index: int) -> DirectiveInterpretation:
    """Calls OpenAI or OpenRouter API with structured JSON output."""
    from openai import OpenAI

    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENROUTER_BASE_URL") or os.getenv("OPENAI_BASE_URL")

    # Auto-detect OpenRouter key (sk-or-v1-...) or provider
    if api_key and api_key.startswith("sk-or-v1-"):
        if not base_url:
            base_url = "https://openrouter.ai/api/v1"
        model = os.getenv("OPENROUTER_MODEL") or os.getenv("OPENAI_MODEL") or "openai/gpt-4o-mini"
    else:
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    client_kwargs: Dict[str, Any] = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url

    client = OpenAI(**client_kwargs)

    prompt = f"Operator Note [Index {note_index}]: \"{note}\"\nOutput the structured directive JSON."

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        temperature=0.0
    )

    raw_json = response.choices[0].message.content or "{}"
    data = _extract_json_from_text(raw_json)
    data["note_index"] = note_index
    return DirectiveInterpretation(**data)


def call_anthropic_interpreter(note: str, note_index: int) -> DirectiveInterpretation:
    """Calls Anthropic Claude API."""
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-20241022")

    prompt = f"Operator Note [Index {note_index}]: \"{note}\"\nOutput the structured directive JSON conforming strictly to the requested schema."

    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0
    )

    text = response.content[0].text
    data = _extract_json_from_text(text)
    data["note_index"] = note_index
    return DirectiveInterpretation(**data)


def interpret_operator_note(note: str, note_index: int) -> DirectiveInterpretation:
    """
    Orchestrates interpretation:
    1. Checks configured LLM provider (OpenRouter, OpenAI, Anthropic).
    2. Falls back to deterministic rule engine if no keys are provided or on API error.
    """
    provider = os.getenv("LLM_PROVIDER", "").lower()
    has_openrouter = bool(os.getenv("OPENROUTER_API_KEY")) or os.getenv("OPENAI_API_KEY", "").startswith("sk-or-v1-")
    has_openai = bool(os.getenv("OPENAI_API_KEY"))
    has_anthropic = bool(os.getenv("ANTHROPIC_API_KEY"))

    if provider in ("openrouter", "openai") or has_openrouter or (has_openai and provider != "anthropic"):
        try:
            return call_openai_interpreter(note, note_index)
        except Exception:
            return deterministic_fallback_interpret(note, note_index)

    if provider == "anthropic" or has_anthropic:
        try:
            return call_anthropic_interpreter(note, note_index)
        except Exception:
            return deterministic_fallback_interpret(note, note_index)

    # Default offline fallback
    return deterministic_fallback_interpret(note, note_index)


def interpret_all_notes(notes: List[str]) -> List[DirectiveInterpretation]:
    """Interprets a list of operator notes in note_index order."""
    return [interpret_operator_note(note, idx) for idx, note in enumerate(notes)]
