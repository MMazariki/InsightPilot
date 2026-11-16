import json

import pytest
import requests

from evaluation import run_fresh_evaluation as fresh
from evaluation.ollama_query_plan_adapter import build_instructions
from evaluation.planner_response import parse_planner_response
from evaluation.query_plan_validator import load_model
from evaluation.semantic_checks import (
    check_semantic_rules,
    describe_catalog,
    dimension_foreign_keys,
    load_catalog,
)


def plan(metrics=None, group_by=None, filters=None):
    return {
        "metrics": metrics or [{"alias": "Net Sales", "expression": {
            "kind": "measure", "name": "Net Sales"}}],
        "group_by": group_by or [],
        "filters": filters or [],
        "order_by": [],
        "limit": None,
    }


def column_filter(table, column, value, operator="eq"):
    return {"type": "column", "table": table, "column": column,
            "operator": operator, "value": value}


def distinct(table, column, alias="Count"):
    return {"alias": alias, "expression": {
        "kind": "distinct_count", "table": table, "column": column}}


# ------------------------------------------------------------------
# Value catalog
# ------------------------------------------------------------------

def test_catalog_is_committed_and_has_no_identifier_columns():
    catalog = load_catalog()
    assert "DimDate[Quarter]" in catalog
    assert "FactSales[Channel]" in catalog
    model_columns = {f"{t['Name']}[{c['Name']}]": c["Type"]
                     for t in load_model()["Tables"] for c in t.get("Columns", [])}
    for field, values in catalog.items():
        assert model_columns[field] == "Text"
        assert not field.endswith(("ID]", "Key]", "Code]", "SKU]"))
        assert 0 < len(values) <= 25


def test_describe_catalog_lists_values():
    text = describe_catalog({"DimDate[Quarter]": ["Q1", "Q2"]})
    assert text == "DimDate[Quarter]: Q1, Q2"


@pytest.mark.parametrize("value", [3, "3", "Quarter 3", "third"])
def test_unknown_quarter_values_rejected_with_allowed_values(value):
    # Shape of every FRESH3-015 plan (both models, both prompts).
    with pytest.raises(ValueError) as error:
        check_semantic_rules(plan(filters=[column_filter("DimDate", "Quarter", value)]))
    message = str(error.value)
    assert "Unknown value" in message
    for allowed in load_catalog()["DimDate[Quarter]"]:
        assert allowed in message


@pytest.mark.parametrize("value", ["Q3", "q3"])
def test_known_values_accepted_case_insensitively(value):
    candidate = plan(filters=[column_filter("DimDate", "Quarter", value)])
    assert check_semantic_rules(candidate) is candidate


def test_uncatalogued_columns_not_value_checked():
    candidate = plan(filters=[column_filter("DimDate", "Year", 2025)])
    assert check_semantic_rules(candidate) is candidate


def test_ne_operator_also_checked():
    with pytest.raises(ValueError, match="Unknown value"):
        check_semantic_rules(plan(filters=[
            column_filter("FactSales", "Channel", "Online", operator="ne")]))


# ------------------------------------------------------------------
# Dimension distinct counts
# ------------------------------------------------------------------

def test_dimension_foreign_keys_from_relationships():
    keys = dimension_foreign_keys(load_model())
    assert keys["DimCustomer"] == {"FactSales": "CustomerKey"}
    assert keys["DimStore"]["FactSales"] == "StoreKey"
    assert "FactSales" not in keys


@pytest.mark.parametrize("metric, group", [
    # 7B FRESH3-007: DimCustomer key grouped by product category.
    (distinct("DimCustomer", "CustomerKey"), ("DimProduct", "Category")),
    # 7B FRESH3-008: DimStore key grouped by brand.
    (distinct("DimStore", "StoreKey"), ("DimProduct", "Brand")),
    # 7B FRESH2-012: DimCustomer business ID grouped by segment is same-table
    # (allowed), so use a fact grouping instead.
    (distinct("DimCustomer", "CustomerID"), ("FactSales", "Channel")),
])
def test_dimension_count_with_other_table_context_rejected(metric, group):
    candidate = plan(metrics=[metric],
                     group_by=[{"table": group[0], "column": group[1]}])
    with pytest.raises(ValueError, match="FactSales\\["):
        check_semantic_rules(candidate)


def test_dimension_count_rejected_inside_divide_and_with_filter_context():
    candidate = plan(metrics=[{"alias": "Avg", "expression": {
        "kind": "divide",
        "numerator": {"kind": "measure", "name": "Gross Profit"},
        "denominator": {"kind": "distinct_count",
                        "table": "DimCustomer", "column": "CustomerKey"}}}],
        filters=[column_filter("FactSales", "Channel", "Delivery")])
    with pytest.raises(ValueError, match="FactSales\\[CustomerKey\\]"):
        check_semantic_rules(candidate)


@pytest.mark.parametrize("metric, group_by", [
    (distinct("DimCustomer", "CustomerKey"), []),
    (distinct("DimCustomer", "CustomerKey"),
     [{"table": "DimCustomer", "column": "CustomerSegment"}]),
    (distinct("FactSales", "CustomerKey"),
     [{"table": "DimProduct", "column": "Category"}]),
    (distinct("DimProduct", "Brand"),
     [{"table": "DimProduct", "column": "Category"}]),
])
def test_valid_distinct_counts_accepted(metric, group_by):
    candidate = plan(metrics=[metric], group_by=group_by)
    assert check_semantic_rules(candidate) is candidate


def test_malformed_plan_parts_are_left_to_the_validator():
    assert check_semantic_rules({"metrics": "x", "filters": [None]}) is not None


# ------------------------------------------------------------------
# Parser and prompt integration
# ------------------------------------------------------------------

def test_parser_semantic_checks_are_opt_in():
    bad = plan(filters=[column_filter("DimDate", "Quarter", "Quarter 3")])
    assert parse_planner_response(json.dumps(bad))["kind"] == "plan"
    with pytest.raises(ValueError, match="Allowed values"):
        parse_planner_response(json.dumps(bad), semantic_checks=True)


def test_semantic_message_preferred_over_type_error():
    bad = plan(filters=[column_filter("DimDate", "Quarter", 3)])
    with pytest.raises(ValueError, match="Allowed values"):
        parse_planner_response(json.dumps(bad), semantic_checks=True)


def test_prompt_v4_extends_v3_with_value_rule_and_catalog():
    v3 = build_instructions("v3")
    v4 = build_instructions("v4")
    assert "COLUMN VALUES:" not in v3
    assert v4.endswith("\n\nCOLUMN VALUES:\n" + describe_catalog())
    assert "listed under\n  COLUMN VALUES" in v4


# ------------------------------------------------------------------
# Repair attempt
# ------------------------------------------------------------------

CASE = {"id": "R", "question": "Show net sales for the third quarter."}
BAD = json.dumps(plan(filters=[column_filter("DimDate", "Quarter", 3)]))
GOOD = json.dumps(plan(filters=[column_filter("DimDate", "Quarter", "Q3")]))


def test_v4_records_semantic_rejection():
    record = fresh.plan_record(CASE, "m", BAD, "v4")
    assert record["status"] == "invalid_plan"
    assert "Allowed values" in record["error"]
    assert fresh.plan_record(CASE, "m", BAD, "v3")["status"] == "invalid_plan"


def test_repair_succeeds_and_keeps_provenance():
    first = fresh.plan_record(CASE, "m", BAD, "v4")
    seen = {}

    def chat(model, messages):
        seen["messages"] = messages
        return GOOD

    repaired = fresh.attempt_repair(CASE, "m", "SYSTEM", first, "v4", chat,
                                    sleep=lambda _: None)
    assert repaired["status"] == "valid_plan"
    assert repaired["repair"]["succeeded"] is True
    assert repaired["repair"]["first_raw_response"] == BAD
    assert "Allowed values" in repaired["repair"]["first_error"]
    roles = [message["role"] for message in seen["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert seen["messages"][2]["content"] == BAD
    assert "Allowed values" in seen["messages"][3]["content"]


def test_repair_failure_stays_invalid():
    first = fresh.plan_record(CASE, "m", BAD, "v4")
    repaired = fresh.attempt_repair(CASE, "m", "S", first, "v4",
                                    lambda model, messages: BAD,
                                    sleep=lambda _: None)
    assert repaired["status"] == "invalid_plan"
    assert repaired["repair"]["succeeded"] is False


def test_repair_can_end_in_clarification():
    first = fresh.plan_record(CASE, "m", BAD, "v4")
    answer = json.dumps({"clarification": {"reason": "ambiguous",
                                           "message": "Which quarter?"}})
    repaired = fresh.attempt_repair(CASE, "m", "S", first, "v4",
                                    lambda model, messages: answer,
                                    sleep=lambda _: None)
    assert repaired["status"] == "clarification"
    assert repaired["repair"]["succeeded"] is True


def test_repair_retries_only_infrastructure_errors():
    first = fresh.plan_record(CASE, "m", BAD, "v4")
    calls = []

    def flaky(model, messages):
        calls.append(1)
        if len(calls) == 1:
            raise requests.ConnectionError("down")
        return GOOD

    repaired = fresh.attempt_repair(CASE, "m", "S", first, "v4", flaky,
                                    sleep=lambda _: None)
    assert repaired["status"] == "valid_plan"
    assert repaired["repair"]["repair_attempts"] == 2


def test_resolve_paths_v4_arm_uses_multi_alias():
    paths = fresh.resolve_paths("fresh_v4", "v4")
    assert paths["plans"].name == "fresh_v4_prompt_v4_query_plans.json"
    assert fresh.comparators_for("fresh_v4")[0].__name__ == (
        "compare_results_with_metric_aliases")


# ------------------------------------------------------------------
# Rule 3 (pipeline v5): grouping by a single-value filtered column
# ------------------------------------------------------------------

def quarter_by_channel(operator="eq", group_quarter=True):
    group_by = [{"table": "FactSales", "column": "Channel"}]
    if group_quarter:
        group_by.append({"table": "DimDate", "column": "Quarter"})
    return plan(group_by=group_by,
                filters=[column_filter("DimDate", "Quarter", "Q2", operator)])


def test_rule3_off_by_default_keeps_v4_behaviour():
    candidate = quarter_by_channel()
    assert check_semantic_rules(candidate) is candidate


def test_rule3_rejects_grouping_by_single_value_filter():
    # Shape of FRESH4-001 (both models, v4): Q2 filter plus Quarter grouping.
    with pytest.raises(ValueError, match="Remove DimDate\[Quarter\] from group_by"):
        check_semantic_rules(quarter_by_channel(),
                             reject_single_value_grouping=True)


def test_rule3_allows_ne_filter_on_grouped_column():
    candidate = quarter_by_channel(operator="ne")
    assert check_semantic_rules(candidate,
                                reject_single_value_grouping=True) is candidate


def test_rule3_allows_filter_without_grouping():
    candidate = quarter_by_channel(group_quarter=False)
    assert check_semantic_rules(candidate,
                                reject_single_value_grouping=True) is candidate


def test_parser_levels():
    payload = json.dumps(quarter_by_channel())
    assert parse_planner_response(payload, semantic_checks=True)["kind"] == "plan"
    with pytest.raises(ValueError, match="constant column"):
        parse_planner_response(payload, semantic_checks="v5")


def test_runner_semantic_levels():
    assert fresh.semantic_level("v3") is False
    assert fresh.semantic_level("v4") is True
    assert fresh.semantic_level("v5") == "v5"
    payload = json.dumps(quarter_by_channel())
    assert fresh.plan_record(CASE, "m", payload, "v4")["status"] == "valid_plan"
    assert fresh.plan_record(CASE, "m", payload, "v5")["status"] == "invalid_plan"


def test_v5_prompt_text_identical_to_v4():
    assert build_instructions("v5") == build_instructions("v4")


def test_rule3_accepts_all_earlier_correct_plans():
    from tests.test_requirement_coverage import canonical_plan

    count = 0
    for name in ("fresh_v1", "fresh_v2", "fresh_v3", "fresh_v4"):
        for case in fresh.load_cases(fresh.resolve_paths(name, "v1")["cases"]):
            if case["category"] == "unsupported":
                continue
            check_semantic_rules(canonical_plan(case["requirement"]),
                                 reject_single_value_grouping=True)
            count += 1
    assert count == 57
