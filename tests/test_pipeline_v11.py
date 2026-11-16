import json

import pytest

from evaluation import run_fresh_evaluation as fresh
from evaluation.answerability import (
    NO_METRIC_NOTE,
    clarification_message,
    gate,
    names_a_metric,
)
from evaluation.ollama_query_plan_adapter import build_instructions
from evaluation.planner_response import parse_planner_response

PLAN = {
    "metrics": [{"alias": "Net Sales",
                 "expression": {"kind": "measure", "name": "Net Sales"}}],
    "group_by": [], "filters": [], "order_by": [], "limit": None,
}


@pytest.mark.parametrize("question", [
    "Give me the numbers.", "Compare them.", "What about last year?",
    "Show me the totals.", "Break it down by quarter.",
])
def test_vague_questions_name_no_metric(question):
    assert names_a_metric(question) is False


@pytest.mark.parametrize("question", [
    "Show net sales by region.", "How many orders per channel?",
    "Revenue by city.", "Which brands sold the most?",
    "Count the distinct orders for each store region.",
    "What is the gross margin percentage by category?",
    "Show the sales target by store region.",
])
def test_specific_questions_name_a_metric(question):
    assert names_a_metric(question) is True


def test_calibration_on_seen_sets_is_locked():
    answerable_flagged = 0
    for name in [f"fresh_v{i}" for i in range(1, 10)]:
        for case in fresh.load_cases(fresh.resolve_paths(name, "v1")["cases"]):
            if case["category"] != "unsupported":
                answerable_flagged += not names_a_metric(case["question"])
    assert answerable_flagged == 0


def test_gate_without_flag_keeps_v9_behaviour():
    def chat(model, messages):
        raise AssertionError("no verifier call expected")

    assert gate("Show me the totals.", PLAN, chat, "m")["passed"] is True


def test_gate_with_flag_verifies_no_metric_question():
    seen = {}

    def chat(model, messages):
        seen["text"] = messages[1]["content"]
        return json.dumps({"faithful": False, "reason": "no metric named"})

    result = gate("Show me the totals.", PLAN, chat, "m",
                  require_metric_reference=True)
    assert result["passed"] is False
    assert result["no_metric_reference"] is True
    assert result["unknown_terms"] == []
    assert NO_METRIC_NOTE in seen["text"]
    assert clarification_message(result).startswith("Which metric do you mean")


def test_gate_with_flag_skips_specific_question():
    def chat(model, messages):
        raise AssertionError("no verifier call expected")

    result = gate("Show net sales by region.", PLAN, chat, "m",
                  require_metric_reference=True)
    assert result["passed"] is True


def test_verifier_prompt_unchanged_without_note():
    seen = {}

    def chat(model, messages):
        seen["text"] = messages[1]["content"]
        return json.dumps({"faithful": True, "reason": "ok"})

    gate("Forecast net sales.", PLAN, chat, "m")
    assert "Note:" not in seen["text"]


def test_runner_v11_wiring():
    assert fresh.METRIC_REFERENCE_PROMPTS == {"v11"}
    assert "v11" in fresh.GATE_PROMPTS
    assert build_instructions("v11") == build_instructions("v10")
    record = {"status": "valid_plan", "plan": PLAN}
    out = fresh.apply_answerability_gate(
        {"question": "Show me the totals."}, "m", record,
        lambda m, msgs: json.dumps({"faithful": False, "reason": "vague"}),
        sleep=lambda _: None, require_metric_reference=True)
    assert out["status"] == "clarification"
    echo = dict(PLAN, group_by=[{"table": "FactSales", "column": "Channel"}],
                metrics=PLAN["metrics"] + [{"alias": "C", "expression": {
                    "kind": "column", "table": "FactSales", "column": "Channel"}}])
    assert parse_planner_response(json.dumps(echo), semantic_checks="v11")[
        "kind"] == "plan"
