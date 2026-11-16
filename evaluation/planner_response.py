"""Planner response envelope: a query plan OR a deliberate clarification.

The v1 planner could only emit a query plan, so unsupported, ambiguous or
write requests were silently turned into unrelated read queries. A planner
response is now exactly one of:

* a query plan (validated by ``query_plan_validator``), or
* ``{"clarification": {"reason": ..., "message": ...}}``
  (validated by ``clarification_schema.json``).

``plan_question`` is the isolated pipeline entry point: request guard ->
planner -> parse/validate. It never executes anything.
"""

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from evaluation.query_plan_validator import validate_query_plan
from evaluation.request_guard import screen_request
from evaluation.semantic_checks import check_semantic_rules

EVAL = Path(__file__).resolve().parent
CLARIFICATION_SCHEMA_FILE = EVAL / "clarification_schema.json"


def validate_clarification(payload):
    schema = json.loads(
        CLARIFICATION_SCHEMA_FILE.read_text(encoding="utf-8-sig")
    )
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda error: str(error.path),
    )
    if errors:
        error = errors[0]
        location = ".".join(map(str, error.absolute_path)) or "root"
        raise ValueError(
            f"Invalid clarification at {location}: {error.message}"
        )
    return payload


def parse_planner_response(raw, semantic_checks=False):
    """Parse raw planner text into a validated plan or clarification.

    Returns ``{"kind": "plan", "plan": ...}`` or
    ``{"kind": "clarification", "reason": ..., "message": ...}``.
    Raises ``ValueError`` for anything else (including mixed responses).
    """

    payload = json.loads(raw) if isinstance(raw, str) else raw

    if not isinstance(payload, dict):
        raise ValueError("Planner response must be a JSON object")

    if "clarification" in payload:
        validate_clarification(payload)
        detail = payload["clarification"]
        return {
            "kind": "clarification",
            "reason": detail["reason"],
            "message": detail["message"],
        }

    if semantic_checks:
        # Run first: its messages are more actionable for repair (e.g. it
        # lists allowed values where the validator only reports a type error).
        check_semantic_rules(payload)
    validate_query_plan(payload)
    return {"kind": "plan", "plan": payload}


def plan_question(question, generate_raw, use_guard=True):
    """Guard -> planner -> validated response. ``generate_raw(question)``
    returns the model's raw text and is NOT called for guarded requests."""

    if use_guard:
        blocked = screen_request(question)
        if blocked is not None:
            detail = blocked["clarification"]
            return {
                "kind": "clarification",
                "reason": detail["reason"],
                "message": detail["message"],
                "source": "request_guard",
            }

    response = parse_planner_response(generate_raw(question))
    response["source"] = "planner"
    return response
