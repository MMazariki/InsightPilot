import json

import pytest

from evaluation import run_fresh_evaluation as fresh
from evaluation.ollama_query_plan_adapter import build_instructions
from evaluation.planner_response import parse_planner_response
from evaluation.semantic_checks import check_expression_kinds, check_semantic_rules
from tests.test_requirement_coverage import canonical_plan


def plan_with(expression):
    return {"metrics": [{"alias": "M", "expression": expression}],
            "group_by": [{"table": "DimProduct", "column": "Category"}],
            "filters": [], "order_by": [], "limit": None}


def test_sum_over_measure_name_points_to_measure():
    # Live app case: "total units sold" -> {"kind": "sum", "column": "Units Sold"}.
    candidate = plan_with({"kind": "sum", "table": "FactSales", "column": "Units Sold"})
    with pytest.raises(ValueError) as error:
        check_expression_kinds(candidate)
    message = str(error.value)
    assert "'sum' is not supported" in message
    assert '{"kind": "measure", "name": "Units Sold"}' in message


def test_nested_metric_kind_inside_divide():
    # 7B FRESH4-016 / FRESH5-014: numerator {"kind": "metric", "name": ...}.
    candidate = plan_with({"kind": "divide",
                           "numerator": {"kind": "metric", "name": "gross sales"},
                           "denominator": {"kind": "measure", "name": "Net Sales"}})
    with pytest.raises(ValueError, match='"name": "Gross Sales"'):
        check_expression_kinds(candidate)


def test_count_kind_on_column_points_to_distinct_count():
    candidate = plan_with({"kind": "count", "table": "FactSales", "column": "OrderID"})
    with pytest.raises(ValueError, match='"kind": "distinct_count"'):
        check_expression_kinds(candidate)


def test_unknown_kind_lists_supported_kinds():
    candidate = plan_with({"kind": "average", "table": "FactSales",
                           "column": "NetSalesSAR"})
    with pytest.raises(ValueError, match="Supported kinds: measure"):
        check_expression_kinds(candidate)


def test_supported_kinds_pass():
    for expression in (
        {"kind": "measure", "name": "Net Sales"},
        {"kind": "distinct_count", "table": "FactSales", "column": "OrderID"},
        {"kind": "divide", "numerator": {"kind": "measure", "name": "Net Sales"},
         "denominator": {"kind": "distinct_count", "table": "FactSales",
                         "column": "OrderID"}},
    ):
        candidate = plan_with(expression)
        assert check_expression_kinds(candidate) is candidate


def test_kind_hints_off_for_v6_on_for_v7():
    payload = json.dumps(plan_with({"kind": "sum", "column": "Units Sold"}))
    with pytest.raises(ValueError, match="not valid under any"):
        parse_planner_response(payload, semantic_checks="v6")
    with pytest.raises(ValueError, match="is an existing measure"):
        parse_planner_response(payload, semantic_checks="v7")


def test_v7_keeps_v6_rules():
    grouped = {"metrics": [{"alias": "Net Sales", "expression": {
        "kind": "measure", "name": "Net Sales"}}],
        "group_by": [{"table": "DimDate", "column": "Quarter"}],
        "filters": [{"type": "column", "table": "DimDate", "column": "Quarter",
                     "operator": "eq", "value": "Q2"}],
        "order_by": [], "limit": None}
    with pytest.raises(ValueError, match="constant column"):
        parse_planner_response(json.dumps(grouped), semantic_checks="v7")
    renamed = dict(grouped, group_by=[], metrics=[{"alias": "Sales, total",
                   "expression": {"kind": "measure", "name": "Net Sales"}}])
    parsed = parse_planner_response(json.dumps(renamed), semantic_checks="v7")
    assert parsed["normalizations"][0]["to"] == "Sales total"


def test_runner_and_prompt_wiring():
    assert fresh.semantic_level("v7") == "v7"
    assert fresh.repair_instruction("v7") == fresh.REPAIR_INSTRUCTION_V6
    assert build_instructions("v7") == build_instructions("v6")
    assert fresh.resolve_paths("fresh_v7", "v7")["plans"].name == (
        "fresh_v7_prompt_v7_query_plans.json")


def test_v7_accepts_all_earlier_correct_plans():
    count = 0
    for name in ("fresh_v1", "fresh_v2", "fresh_v3", "fresh_v4", "fresh_v5",
                 "fresh_v6"):
        for case in fresh.load_cases(fresh.resolve_paths(name, "v1")["cases"]):
            if case["category"] == "unsupported":
                continue
            check_semantic_rules(canonical_plan(case["requirement"]),
                                 reject_single_value_grouping=True,
                                 reference_hints=True,
                                 expression_kind_hints=True)
            count += 1
    assert count == 87
