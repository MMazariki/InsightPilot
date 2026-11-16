import json

import pytest

from evaluation import run_fresh_evaluation as fresh
from evaluation.ollama_query_plan_adapter import build_instructions
from evaluation.plan_normalization import sanitize_aliases
from evaluation.planner_response import parse_planner_response
from evaluation.semantic_checks import check_reference_hints, check_semantic_rules
from tests.test_requirement_coverage import canonical_plan


def plan(metrics=None, group_by=None, filters=None, order_by=None, limit=None):
    return {
        "metrics": metrics or [{"alias": "Net Sales", "expression": {
            "kind": "measure", "name": "Net Sales"}}],
        "group_by": group_by or [],
        "filters": filters or [],
        "order_by": order_by or [],
        "limit": limit,
    }


def measure(name, alias=None):
    return {"alias": alias or name, "expression": {"kind": "measure", "name": name}}


# ------------------------------------------------------------------
# Rule 0: reference hints
# ------------------------------------------------------------------

@pytest.mark.parametrize("name, column", [
    ("Channel", "FactSales[Channel]"),          # 3B FRESH5-010
    ("PaymentMethod", "FactSales[PaymentMethod]"),  # 3B FRESH5-016
])
def test_column_used_as_measure_gets_fix_it_hint(name, column):
    with pytest.raises(ValueError) as error:
        check_reference_hints(plan(metrics=[measure(name)]))
    message = str(error.value)
    assert f"'{name}' is not a measure" in message
    assert column in message
    assert "group_by" in message
    assert "clarification" not in message


def test_column_on_wrong_table_gets_owner_hint():
    # 3B FRESH5-004: DimStore[Brand].
    with pytest.raises(ValueError, match=r"DimProduct\[Brand\]"):
        check_reference_hints(plan(group_by=[{"table": "DimStore", "column": "Brand"}]))


def test_wrong_table_in_filter_and_distinct_count():
    with pytest.raises(ValueError, match=r"FactSales\[Channel\]"):
        check_reference_hints(plan(filters=[{
            "type": "column", "table": "DimStore", "column": "Channel",
            "operator": "eq", "value": "Delivery"}]))
    with pytest.raises(ValueError, match=r"FactSales\[OrderID\]"):
        check_reference_hints(plan(metrics=[{"alias": "Orders", "expression": {
            "kind": "distinct_count", "table": "DimCustomer", "column": "OrderID"}}]))


def test_unqualified_sort_field_gets_qualified_hint():
    # 7B FRESH5-007: order_by "YearMonth" while grouping by DimDate[YearMonth].
    candidate = plan(group_by=[{"table": "DimDate", "column": "YearMonth"}],
                     order_by=[{"field": "YearMonth", "direction": "asc"}])
    with pytest.raises(ValueError, match=r"DimDate\[YearMonth\]"):
        check_reference_hints(candidate)


def test_undefined_measure_keeps_clarification_path():
    # e.g. "Average Delivery Time": must not be pushed into a substitute.
    with pytest.raises(ValueError) as error:
        check_reference_hints(plan(metrics=[measure("Average Delivery Time")]))
    message = str(error.value)
    assert "No measure named 'Average Delivery Time'" in message
    assert "return a clarification" in message
    assert "Net Sales" in message


def test_hint_inside_divide():
    candidate = plan(metrics=[{"alias": "Ratio", "expression": {
        "kind": "divide", "numerator": {"kind": "measure", "name": "Net Sales"},
        "denominator": {"kind": "measure", "name": "Channel"}}}])
    with pytest.raises(ValueError, match="is not a measure"):
        check_reference_hints(candidate)


def test_hints_off_by_default():
    candidate = plan(metrics=[measure("Channel")])
    assert check_semantic_rules(candidate) is candidate


def test_hints_and_rule3_accept_all_earlier_correct_plans():
    count = 0
    for name in ("fresh_v1", "fresh_v2", "fresh_v3", "fresh_v4", "fresh_v5"):
        for case in fresh.load_cases(fresh.resolve_paths(name, "v1")["cases"]):
            if case["category"] == "unsupported":
                continue
            check_semantic_rules(canonical_plan(case["requirement"]),
                                 reject_single_value_grouping=True,
                                 reference_hints=True)
            count += 1
    assert count == 73


# ------------------------------------------------------------------
# Alias normalisation
# ------------------------------------------------------------------

def test_alias_with_comma_is_sanitized_and_references_follow():
    # 7B FRESH5-014: "Net Sales Below SAR 20,000".
    original = plan(
        metrics=[measure("Net Sales", alias="Net Sales Below SAR 20,000")],
        group_by=[{"table": "DimProduct", "column": "Subcategory"}],
        filters=[{"type": "metric", "alias": "Net Sales Below SAR 20,000",
                  "operator": "lt", "value": 20000}],
        order_by=[{"field": "Net Sales Below SAR 20,000", "direction": "asc"}])
    cleaned, changes = sanitize_aliases(original)
    assert changes == [{"kind": "alias_sanitized",
                        "from": "Net Sales Below SAR 20,000",
                        "to": "Net Sales Below SAR 20 000"}]
    assert cleaned["filters"][0]["alias"] == "Net Sales Below SAR 20 000"
    assert cleaned["order_by"][0]["field"] == "Net Sales Below SAR 20 000"
    assert original["metrics"][0]["alias"] == "Net Sales Below SAR 20,000"
    check_semantic_rules(cleaned)


def test_sanitize_keeps_valid_aliases_and_resolves_collisions():
    original = plan(metrics=[measure("Net Sales", alias="Sales!"),
                             measure("Gross Sales", alias="Sales")])
    cleaned, changes = sanitize_aliases(original)
    assert [m["alias"] for m in cleaned["metrics"]] == ["Sales 2", "Sales"]
    assert len(changes) == 1


@pytest.mark.parametrize("alias, expected", [
    ("1st Net Sales", "st Net Sales"),
    ("@@@", "Metric"),
    ('Sales" & EVALUATE', "Sales EVALUATE"),
])
def test_sanitize_edge_cases(alias, expected):
    cleaned, _ = sanitize_aliases(plan(metrics=[measure("Net Sales", alias=alias)]))
    assert cleaned["metrics"][0]["alias"] == expected


def test_sanitize_leaves_malformed_plans_alone():
    assert sanitize_aliases({"metrics": "x"}) == ({"metrics": "x"}, [])
    assert sanitize_aliases(None) == (None, [])


# ------------------------------------------------------------------
# Parser, runner and prompt wiring
# ------------------------------------------------------------------

def test_parser_v6_normalises_and_reports():
    payload = json.dumps(plan(metrics=[measure("Net Sales", alias="Sales, total")]))
    with pytest.raises(ValueError, match="Unsafe or invalid metric alias"):
        parse_planner_response(payload, semantic_checks="v5")
    parsed = parse_planner_response(payload, semantic_checks="v6")
    assert parsed["plan"]["metrics"][0]["alias"] == "Sales total"
    assert parsed["normalizations"][0]["from"] == "Sales, total"


def test_parser_v6_applies_rule3_and_hints():
    grouped = plan(group_by=[{"table": "DimDate", "column": "Quarter"}],
                   filters=[{"type": "column", "table": "DimDate", "column": "Quarter",
                             "operator": "eq", "value": "Q2"}])
    with pytest.raises(ValueError, match="constant column"):
        parse_planner_response(json.dumps(grouped), semantic_checks="v6")
    with pytest.raises(ValueError, match="is not a measure"):
        parse_planner_response(json.dumps(plan(metrics=[measure("Channel")])),
                               semantic_checks="v6")


def test_runner_v6_levels_and_repair_wording():
    assert fresh.semantic_level("v6") == "v6"
    assert fresh.repair_instruction("v5") == fresh.REPAIR_INSTRUCTION
    assert "not a reason to decline" in fresh.repair_instruction("v6")
    assert fresh.comparators_for("fresh_v6")[0].__name__ == (
        "compare_results_with_metric_aliases")


def test_plan_record_v6_stores_normalised_plan_and_original_raw():
    raw = json.dumps(plan(metrics=[measure("Net Sales", alias="Sales, total")]))
    record = fresh.plan_record({"id": "X"}, "m", raw, "v6")
    assert record["status"] == "valid_plan"
    assert record["plan"]["metrics"][0]["alias"] == "Sales total"
    assert record["raw_response"] == raw
    assert record["normalizations"][0]["kind"] == "alias_sanitized"


def test_repair_uses_v6_wording():
    raw = json.dumps(plan(metrics=[measure("Channel")]))
    first = fresh.plan_record({"id": "X", "question": "q"}, "m", raw, "v6")
    seen = {}

    def chat(model, messages):
        seen["text"] = messages[-1]["content"]
        return json.dumps(plan())

    repaired = fresh.attempt_repair({"id": "X", "question": "q"}, "m", "S",
                                    first, "v6", chat, sleep=lambda _: None)
    assert repaired["status"] == "valid_plan"
    assert "not a reason to decline" in seen["text"]
    assert "is not a measure" in seen["text"]


def test_v6_prompt_text_identical_to_v4():
    assert build_instructions("v6") == build_instructions("v4")
