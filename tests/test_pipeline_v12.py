import json

import pytest

from evaluation import run_fresh_evaluation as fresh
from evaluation.answerability import gate, names_a_metric
from evaluation.ollama_query_plan_adapter import build_instructions
from evaluation.planner_response import parse_planner_response

PLAN = {
    "metrics": [{"alias": "Net Sales",
                 "expression": {"kind": "measure", "name": "Net Sales"}}],
    "group_by": [], "filters": [], "order_by": [], "limit": None,
}


@pytest.mark.parametrize("question", [
    "Compare the store types.", "Show the figures by channel.",
    "Rank the regions.", "Break it down by region.",
])
def test_grouping_entities_name_no_metric_when_strict(question):
    assert names_a_metric(question) is True  # v11 behaviour
    assert names_a_metric(question, strict=True) is False


@pytest.mark.parametrize("question", [
    "Units by brand.", "Profit by store type.", "Discounts by quarter.",
    "How many stores per region?", "Number of brands by category.",
    "Customers per segment who bought something.",
    "Which brands sold the most?", "Orders by channel.",
])
def test_quantities_and_counts_still_name_a_metric_when_strict(question):
    assert names_a_metric(question, strict=True) is True


def test_strict_calibration_on_seen_sets_is_locked():
    answerable_flagged = 0
    unsupported_flagged = 0
    for name in [f"fresh_v{i}" for i in range(1, 11)]:
        for case in fresh.load_cases(fresh.resolve_paths(name, "v1")["cases"]):
            flagged = not names_a_metric(case["question"], strict=True)
            if case["category"] == "unsupported":
                unsupported_flagged += flagged
            else:
                answerable_flagged += flagged
    assert answerable_flagged == 0
    assert unsupported_flagged == 33


def test_gate_strict_flag_verifies_entity_only_question():
    def no_chat(model, messages):
        raise AssertionError("no verifier call expected")

    assert gate("Rank the regions.", PLAN, no_chat, "m",
                require_metric_reference=True)["passed"] is True
    result = gate(
        "Rank the regions.", PLAN,
        lambda m, msgs: json.dumps({"faithful": False, "reason": "vague"}),
        "m", require_metric_reference=True, strict_metric_words=True)
    assert result["passed"] is False
    assert result["no_metric_reference"] is True


def test_runner_v12_wiring():
    assert fresh.STRICT_METRIC_PROMPTS == {"v12"}
    assert {"v12"} <= fresh.METRIC_REFERENCE_PROMPTS & fresh.GATE_PROMPTS
    assert "v12" in fresh.REPAIR_PROMPTS
    assert fresh.semantic_level("v12") == "v12"
    assert build_instructions("v12") == build_instructions("v11")
    record = {"status": "valid_plan", "plan": PLAN}
    out = fresh.apply_answerability_gate(
        {"question": "Rank the regions."}, "m", record,
        lambda m, msgs: json.dumps({"faithful": False, "reason": "vague"}),
        sleep=lambda _: None, require_metric_reference=True,
        strict_metric_words=True)
    assert out["status"] == "clarification"
    echo = dict(PLAN, group_by=[{"table": "FactSales", "column": "Channel"}],
                metrics=PLAN["metrics"] + [{"alias": "C", "expression": {
                    "kind": "column", "table": "FactSales", "column": "Channel"}}])
    parsed = parse_planner_response(json.dumps(echo), semantic_checks="v12")
    assert parsed["kind"] == "plan"
    assert parsed == parse_planner_response(json.dumps(echo),
                                            semantic_checks="v10")
