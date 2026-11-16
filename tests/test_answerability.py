import json

import pytest

from evaluation import run_fresh_evaluation as fresh
from evaluation.answerability import (
    build_vocabulary,
    clarification_message,
    describe_plan,
    gate,
    unknown_terms,
    verify,
)
from evaluation.ollama_query_plan_adapter import build_instructions
from evaluation.planner_response import parse_planner_response

PLAN = {
    "metrics": [{"alias": "Units Sold",
                 "expression": {"kind": "measure", "name": "Units Sold"}}],
    "group_by": [{"table": "DimProduct", "column": "Brand"}],
    "filters": [{"type": "column", "table": "FactSales", "column": "Channel",
                 "operator": "eq", "value": "Delivery"}],
    "order_by": [{"field": "Units Sold", "direction": "desc"}],
    "limit": 3,
}
VOCAB = build_vocabulary()


# ------------------------------------------------------------------
# Trigger
# ------------------------------------------------------------------

@pytest.mark.parametrize("question, expected", [
    ("Show the number of returned items by brand.", ["returned"]),
    ("Forecast next quarter's net sales.", ["forecast", "next"]),
    ("How are the numbers looking?", ["looking"]),
    ("Show staff overtime hours by store.", ["staff", "overtime", "hours"]),
])
def test_unknown_terms_flag_unsupported_wording(question, expected):
    assert unknown_terms(question, VOCAB) == expected


@pytest.mark.parametrize("question", [
    "Show net sales by store region, highest first.",
    "How many distinct customers bought each brand?",
    "What were gross sales for click and collect orders by store region?",
    "Show units sold by day name for weekend days only.",
    "Which three store cities had the lowest net sales?",
    "Show net sales for the second quarter by sales channel.",
    "What is the revenue per order in Riyadh?",
])
def test_answerable_wording_not_flagged(question):
    assert unknown_terms(question, VOCAB) == []


def test_trigger_rates_on_seen_sets_are_locked():
    # Calibration record (fresh_v1-v8 were already seen): the generic
    # vocabulary was extended until no answerable question triggered.
    answerable = unsupported = triggered_answerable = triggered_unsupported = 0
    for name in ("fresh_v1", "fresh_v2", "fresh_v3", "fresh_v4", "fresh_v5",
                 "fresh_v6", "fresh_v7", "fresh_v8"):
        for case in fresh.load_cases(fresh.resolve_paths(name, "v1")["cases"]):
            flagged = bool(unknown_terms(case["question"], VOCAB))
            if case["category"] == "unsupported":
                unsupported += 1
                triggered_unsupported += flagged
            else:
                answerable += 1
                triggered_answerable += flagged
    assert (answerable, unsupported) == (113, 48)
    assert triggered_answerable == 0
    assert triggered_unsupported == 43


# ------------------------------------------------------------------
# Plan description and verifier
# ------------------------------------------------------------------

def test_describe_plan_is_plain_english():
    text = describe_plan(PLAN)
    assert text == ("Compute the measure Units Sold, for each DimProduct[Brand], "
                    "where FactSales[Channel] = 'Delivery', sorted by Units Sold "
                    "desc, keeping the first 3 rows.")


def test_describe_divide_and_distinct_count():
    plan = dict(PLAN, filters=[], order_by=[], limit=None, metrics=[{
        "alias": "x", "expression": {
            "kind": "divide", "numerator": {"kind": "measure", "name": "Net Sales"},
            "denominator": {"kind": "distinct_count", "table": "FactSales",
                            "column": "OrderID"}}}])
    assert "divided by (the number of distinct OrderID values in FactSales)" in (
        describe_plan(plan))


def test_verify_parses_verdict_and_sends_context():
    seen = {}

    def chat(model, messages):
        seen["messages"] = messages
        return json.dumps({"faithful": False, "reason": "returns not in model"})

    verdict = verify("Show returned items by brand.", PLAN, ["returned"], chat,
                     "m", ["Units Sold"])
    assert verdict["faithful"] is False
    assert verdict["reason"] == "returns not in model"
    user = seen["messages"][1]["content"]
    assert "Question: Show returned items by brand." in user
    assert "Compute the measure Units Sold" in user
    assert "not found in the model: returned" in user


@pytest.mark.parametrize("raw", ["not json", json.dumps({"faithful": "no"}),
                                 json.dumps(["x"])])
def test_unusable_verdict_does_not_block_but_is_recorded(raw):
    verdict = verify("q", PLAN, ["x"], lambda m, msgs: raw, "m", [])
    assert verdict["faithful"] is True
    assert verdict["verifier_error"] is True


# ------------------------------------------------------------------
# Gate
# ------------------------------------------------------------------

def test_gate_skips_verifier_when_nothing_unknown():
    def chat(model, messages):
        raise AssertionError("verifier must not be called")

    result = gate("Show units sold by brand.", PLAN, chat, "m", VOCAB)
    assert result == {"passed": True, "unknown_terms": [], "verdict": None}


def test_gate_blocks_on_unfaithful_verdict():
    result = gate("Show the number of returned items by brand.", PLAN,
                  lambda m, msgs: json.dumps({"faithful": False, "reason": "r"}),
                  "m", VOCAB)
    assert result["passed"] is False
    assert result["unknown_terms"] == ["returned"]
    assert "returned" in clarification_message(result)


def test_gate_passes_on_faithful_verdict():
    result = gate("Show units sold by brand for the busiest channel.", PLAN,
                  lambda m, msgs: json.dumps({"faithful": True, "reason": "ok"}),
                  "m", VOCAB)
    assert result["passed"] is True
    assert result["unknown_terms"] == ["busiest"]


# ------------------------------------------------------------------
# Runner wiring
# ------------------------------------------------------------------

def test_runner_gate_turns_plan_into_clarification():
    record = {"status": "valid_plan", "plan": PLAN}
    case = {"question": "Show the number of returned items by brand."}
    gated = fresh.apply_answerability_gate(
        case, "m", record,
        lambda m, msgs: json.dumps({"faithful": False, "reason": "r"}),
        sleep=lambda _: None)
    assert gated["status"] == "clarification"
    assert gated["gated_plan"] == PLAN
    assert "plan" not in gated
    assert gated["answerability"]["passed"] is False


def test_runner_gate_leaves_non_plans_and_clean_questions_alone():
    invalid = {"status": "invalid_plan"}
    assert fresh.apply_answerability_gate({"question": "x"}, "m", invalid,
                                          None) is invalid
    record = {"status": "valid_plan", "plan": PLAN}
    out = fresh.apply_answerability_gate(
        {"question": "Show units sold by brand."}, "m", record, None)
    assert out["status"] == "valid_plan"
    assert out["answerability"]["unknown_terms"] == []


def test_v9_wiring():
    assert "v9" in fresh.GATE_PROMPTS
    assert fresh.semantic_level("v9") == "v9"
    assert build_instructions("v9") == build_instructions("v7")
    payload = json.dumps(dict(PLAN, metrics=[{"alias": "M", "expression": {
        "kind": "sum", "column": "Units Sold"}}]))
    with pytest.raises(ValueError, match="is an existing measure"):
        parse_planner_response(payload, semantic_checks="v9")
    assert fresh.resolve_paths("fresh_v9", "v9")["plans"].name == (
        "fresh_v9_prompt_v9_query_plans.json")
