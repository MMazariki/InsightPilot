import json

import pytest

from evaluation import run_fresh_evaluation as fresh
from evaluation.ollama_query_plan_adapter import build_instructions
from evaluation.plan_normalization import drop_grouped_column_metrics
from evaluation.planner_response import parse_planner_response

# Live app case: "Show gross profit by sales channel".
ECHO_PLAN = {
    "metrics": [
        {"alias": "Gross Profit",
         "expression": {"kind": "measure", "name": "Gross Profit"}},
        {"alias": "Sales Channel",
         "expression": {"kind": "column", "table": "FactSales", "column": "Channel"}},
    ],
    "group_by": [{"table": "FactSales", "column": "Channel"}],
    "filters": [],
    "order_by": [{"field": "Sales Channel", "direction": "asc"}],
    "limit": None,
}


def test_echo_metric_dropped_and_sort_redirected():
    cleaned, changes = drop_grouped_column_metrics(ECHO_PLAN)
    assert [m["alias"] for m in cleaned["metrics"]] == ["Gross Profit"]
    assert cleaned["order_by"] == [{"field": "FactSales[Channel]", "direction": "asc"}]
    assert changes == [{"kind": "grouped_column_metric_dropped",
                        "alias": "Sales Channel", "column": "FactSales[Channel]"}]
    assert len(ECHO_PLAN["metrics"]) == 2  # input not mutated


@pytest.mark.parametrize("kind", ["column", "dimension", "field", "attribute"])
def test_column_like_kinds_recognised(kind):
    plan = json.loads(json.dumps(ECHO_PLAN))
    plan["metrics"][1]["expression"]["kind"] = kind
    assert len(drop_grouped_column_metrics(plan)[0]["metrics"]) == 1


def test_column_metric_not_in_group_by_is_kept_for_validator():
    plan = json.loads(json.dumps(ECHO_PLAN))
    plan["group_by"] = [{"table": "DimStore", "column": "Region"}]
    assert drop_grouped_column_metrics(plan) == (plan, [])


def test_never_removes_the_only_metric():
    plan = json.loads(json.dumps(ECHO_PLAN))
    plan["metrics"] = plan["metrics"][1:]
    assert drop_grouped_column_metrics(plan) == (plan, [])


def test_malformed_input_unchanged():
    assert drop_grouped_column_metrics(None) == (None, [])
    assert drop_grouped_column_metrics({"metrics": "x"}) == ({"metrics": "x"}, [])


def test_parser_v9_rejects_but_v10_accepts():
    payload = json.dumps(ECHO_PLAN)
    with pytest.raises(ValueError, match="Expression kind 'column'"):
        parse_planner_response(payload, semantic_checks="v9")
    parsed = parse_planner_response(payload, semantic_checks="v10")
    assert parsed["kind"] == "plan"
    assert parsed["normalizations"][0]["kind"] == "grouped_column_metric_dropped"


def test_v10_combines_with_alias_normalisation_and_keeps_v7_rules():
    plan = json.loads(json.dumps(ECHO_PLAN))
    plan["metrics"][0]["alias"] = "Gross Profit, total"
    parsed = parse_planner_response(json.dumps(plan), semantic_checks="v10")
    kinds = [change["kind"] for change in parsed["normalizations"]]
    assert kinds == ["grouped_column_metric_dropped", "alias_sanitized"]
    grouped = dict(ECHO_PLAN, filters=[{"type": "column", "table": "FactSales",
                                        "column": "Channel", "operator": "eq",
                                        "value": "Delivery"}])
    with pytest.raises(ValueError, match="constant column"):
        parse_planner_response(json.dumps(grouped), semantic_checks="v10")


def test_runner_wiring():
    assert "v10" in fresh.GATE_PROMPTS and "v10" in fresh.REPAIR_PROMPTS
    assert fresh.semantic_level("v10") == "v10"
    assert fresh.repair_instruction("v10") == fresh.REPAIR_INSTRUCTION_V6
    assert build_instructions("v10") == build_instructions("v9")
