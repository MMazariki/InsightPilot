import json

import pytest

from evaluation import run_fresh_evaluation as fresh
from evaluation.ollama_query_plan_adapter import build_instructions
from evaluation.planner_response import (
    parse_planner_response,
    plan_question,
    validate_clarification,
)
from evaluation.request_guard import screen_request

PLAN = {
    "metrics": [{"alias": "Net Sales",
                 "expression": {"kind": "measure", "name": "Net Sales"}}],
    "group_by": [], "filters": [], "order_by": [], "limit": None,
}


def clarification(reason="ambiguous", message="Which metric do you mean?"):
    return {"clarification": {"reason": reason, "message": message}}


# ------------------------------------------------------------------
# Request guard
# ------------------------------------------------------------------

@pytest.mark.parametrize("question", [
    "Delete all sales transactions from 2025.",
    "Please remove the Jeddah store records.",
    "Can you drop the FactTarget table?",
    "Update the sales target for Q4 to 2 million.",
    "I want you to insert a new row for store 12.",
    "Add a new product category called Toys.",
    "Show net sales by region and then delete the old records.",
    "Rename the Gross Profit measure to GP.",
    "Set all customer segments to Regular.",
    "Truncate the sales table",
])
def test_guard_blocks_write_requests(question):
    result = screen_request(question)
    assert result is not None
    validate_clarification(result)
    assert result["clarification"]["reason"] == "write_request"


@pytest.mark.parametrize("question", [
    "Show net sales by region.",
    "Which categories saw the biggest drop in net sales?",
    "Add up net sales by region.",
    "What is the change in gross profit by quarter?",
    "Show net sales by channel, excluding the Delivery channel.",
    "Which stores had a drop in customers?",
    "How many products are in each category?",
    "Show the top five stores by net sales.",
    "List customers by segment.",
])
def test_guard_allows_analytical_questions(question):
    assert screen_request(question) is None


def test_guard_rejects_non_string():
    with pytest.raises(TypeError):
        screen_request(None)


# ------------------------------------------------------------------
# Planner response envelope
# ------------------------------------------------------------------

def test_parse_plan():
    assert parse_planner_response(json.dumps(PLAN)) == {"kind": "plan", "plan": PLAN}


def test_parse_clarification():
    parsed = parse_planner_response(json.dumps(clarification("undefined_metric")))
    assert parsed == {"kind": "clarification", "reason": "undefined_metric",
                      "message": "Which metric do you mean?"}


@pytest.mark.parametrize("payload, match", [
    (clarification(reason="because"), "Invalid clarification"),
    (clarification(message=""), "Invalid clarification"),
    ({"clarification": {"reason": "ambiguous"}}, "Invalid clarification"),
    (dict(PLAN, **clarification()), "Invalid clarification"),
    ({"clarification": {"reason": "ambiguous", "message": "x", "plan": {}}},
     "Invalid clarification"),
    ([PLAN], "must be a JSON object"),
    (dict(PLAN, metrics=[]), "Invalid query plan"),
])
def test_parse_rejects_malformed_responses(payload, match):
    with pytest.raises(ValueError, match=match):
        parse_planner_response(json.dumps(payload))


def test_parse_rejects_non_json():
    with pytest.raises(ValueError):
        parse_planner_response("not json")


# ------------------------------------------------------------------
# Guarded pipeline
# ------------------------------------------------------------------

def test_guard_short_circuits_planner():
    def planner(question):
        raise AssertionError("planner must not be called")

    result = plan_question("Delete all sales from 2025.", planner)
    assert result["kind"] == "clarification"
    assert result["reason"] == "write_request"
    assert result["source"] == "request_guard"


def test_guard_can_be_disabled():
    result = plan_question("Delete all sales from 2025.",
                           lambda q: json.dumps(PLAN), use_guard=False)
    assert result == {"kind": "plan", "plan": PLAN, "source": "planner"}


def test_planner_clarification_passed_through():
    result = plan_question("What is our NPS?",
                           lambda q: json.dumps(clarification("undefined_metric")))
    assert result["kind"] == "clarification"
    assert result["source"] == "planner"


# ------------------------------------------------------------------
# Prompt versions
# ------------------------------------------------------------------

def test_v1_instructions_unchanged_by_v2_support():
    v1 = build_instructions()
    assert v1 == build_instructions("v1")
    assert "CLARIFICATION" not in v1
    assert "WHEN NOT TO PLAN" not in v1


def test_v2_instructions_add_clarification_contract():
    v2 = build_instructions("v2")
    assert "WHEN NOT TO PLAN" in v2
    assert "CLARIFICATION JSON SCHEMA:" in v2
    assert "QUERY PLAN JSON SCHEMA:" in v2


# ------------------------------------------------------------------
# Runner: v2 recording and scoring
# ------------------------------------------------------------------

CASE = {"id": "X"}


def test_plan_record_v1_treats_clarification_as_invalid():
    record = fresh.plan_record(CASE, "m", json.dumps(clarification()), "v1")
    assert record["status"] == "invalid_plan"


def test_plan_record_v2_records_clarification():
    record = fresh.plan_record(CASE, "m", json.dumps(clarification()), "v2")
    assert record["status"] == "clarification"
    assert record["clarification"]["reason"] == "ambiguous"
    assert "plan" not in record


def test_plan_record_v2_still_accepts_plans_and_rejects_bad_output():
    assert fresh.plan_record(CASE, "m", json.dumps(PLAN), "v2")["status"] == "valid_plan"
    bad = fresh.plan_record(CASE, "m", json.dumps(clarification("nope")), "v2")
    assert bad["status"] == "invalid_plan"


UNSUPPORTED = {"id": "U", "category": "unsupported",
               "requirement": {"supported": False},
               "expected_reason": "undefined_metric"}


@pytest.mark.parametrize("record, passed, declined, outcome", [
    ({"status": "clarification",
      "clarification": {"reason": "undefined_metric", "message": "m"}},
     True, True, "declined"),
    ({"status": "blocked_by_guard",
      "clarification": {"reason": "write_request", "message": "m"}},
     True, True, "declined"),
    ({"status": "invalid_plan"}, True, False, "not_answered"),
    ({"status": "valid_plan", "plan": PLAN}, False, False, "answered"),
])
def test_unsupported_scoring(record, passed, declined, outcome):
    result = fresh.evaluate_case(UNSUPPORTED, record, None, None)
    assert (result["passed"], result["declined"], result["outcome"]) == (
        passed, declined, outcome)


def test_reason_match_reported_but_not_gating():
    wrong = {"status": "clarification",
             "clarification": {"reason": "ambiguous", "message": "m"}}
    result = fresh.evaluate_case(UNSUPPORTED, wrong, None, None)
    assert result["passed"] is True
    assert result["reason_matches"] is False


def test_clarification_on_answerable_case_is_over_refusal():
    case = {"id": "A", "category": "answerable",
            "requirement": {"metrics": [{"kind": "measure", "name": "Net Sales"}],
                            "group_by": [], "limit": None}}
    reference = {"status": "success", "columns": [], "rows": []}
    record = {"status": "clarification",
              "clarification": {"reason": "ambiguous", "message": "m"}}

    def execute(dax):
        raise AssertionError("must not execute")

    result = fresh.evaluate_case(case, record, reference, execute)
    assert result["outcome"] == "over_refusal"
    assert result["passed"] is False
    summary = fresh.summarise([result])
    assert summary["answerable_over_refusals"] == 1


def test_resolve_paths_keeps_legacy_names_and_separates_arms():
    legacy = fresh.resolve_paths()
    assert legacy["plans"].name == "fresh_v1_query_plans.json"
    assert legacy["report"].name == "fresh_v1_evaluation.json"
    v2 = fresh.resolve_paths("fresh_v2", "v2")
    v1 = fresh.resolve_paths("fresh_v2", "v1")
    assert v2["references"] == v1["references"]
    assert v2["plans"] != v1["plans"]
    assert v2["report"] != v1["report"]
    assert v2["plans"].name == "fresh_v2_prompt_v2_query_plans.json"
    with pytest.raises(ValueError):
        fresh.resolve_paths("nope", "v1")
