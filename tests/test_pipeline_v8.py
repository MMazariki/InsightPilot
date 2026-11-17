import json

import pytest

from evaluation import run_fresh_evaluation as fresh
from evaluation.ollama_query_plan_adapter import build_instructions
from evaluation.planner_response import parse_planner_response
from evaluation.semantic_checks import (
    COLUMN_AS_MEASURE_DECLINE,
    check_reference_hints,
    check_semantic_rules,
)
from tests.test_requirement_coverage import canonical_plan


def plan_with_measure(name):
    return {"metrics": [{"alias": "M", "expression": {"kind": "measure", "name": name}}],
            "group_by": [{"table": "FactSales", "column": "Channel"}],
            "filters": [], "order_by": [], "limit": None}


def test_column_hint_has_decline_path_only_in_v8():
    # fresh_v7 7B: "total marketing spend by channel" -> measure "Total Orders"
    # (a FactSales column) -> v6/v7 hint -> unrelated query was executed.
    # "Total Orders" was taken out of the app's model on 2026-11-17, so the
    # same case is checked with another FactSales column.
    candidate = plan_with_measure("NetSalesSAR")
    with pytest.raises(ValueError) as v7:
        check_reference_hints(candidate)
    with pytest.raises(ValueError) as v8:
        check_reference_hints(candidate, column_hint_declines=True)
    assert "clarification" not in str(v7.value)
    assert str(v8.value).endswith(COLUMN_AS_MEASURE_DECLINE)
    assert "Use it in group_by" in str(v8.value)


def test_other_hints_unchanged_in_v8():
    with pytest.raises(ValueError) as error:
        check_reference_hints(plan_with_measure("Marketing Spend"),
                              column_hint_declines=True)
    assert COLUMN_AS_MEASURE_DECLINE not in str(error.value)
    assert "No measure named 'Marketing Spend'" in str(error.value)


def test_parser_levels_v7_vs_v8():
    payload = json.dumps(plan_with_measure("Channel"))
    with pytest.raises(ValueError) as v7:
        parse_planner_response(payload, semantic_checks="v7")
    with pytest.raises(ValueError) as v8:
        parse_planner_response(payload, semantic_checks="v8")
    assert COLUMN_AS_MEASURE_DECLINE not in str(v7.value)
    assert COLUMN_AS_MEASURE_DECLINE in str(v8.value)


def test_v8_keeps_v7_rules():
    payload = json.dumps(plan_with_measure("Net Sales") | {"metrics": [
        {"alias": "M", "expression": {"kind": "sum", "column": "Units Sold"}}]})
    with pytest.raises(ValueError, match="is an existing measure"):
        parse_planner_response(payload, semantic_checks="v8")


def test_runner_and_prompt_wiring():
    assert fresh.semantic_level("v8") == "v8"
    assert fresh.repair_instruction("v8") == fresh.REPAIR_INSTRUCTION_V6
    assert build_instructions("v8") == build_instructions("v7")
    assert fresh.resolve_paths("fresh_v8", "v8")["plans"].name == (
        "fresh_v8_prompt_v8_query_plans.json")


def test_v8_accepts_all_earlier_correct_plans():
    count = 0
    for name in ("fresh_v1", "fresh_v2", "fresh_v3", "fresh_v4", "fresh_v5",
                 "fresh_v6", "fresh_v7"):
        for case in fresh.load_cases(fresh.resolve_paths(name, "v1")["cases"]):
            if case["category"] == "unsupported":
                continue
            check_semantic_rules(canonical_plan(case["requirement"]),
                                 reject_single_value_grouping=True,
                                 reference_hints=True,
                                 expression_kind_hints=True,
                                 column_hint_declines=True)
            count += 1
    assert count == 101
