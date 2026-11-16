import pytest

from evaluation import run_fresh_evaluation as fresh
from evaluation.alias_aware_comparator import (
    compare_results_with_metric_alias,
    compare_results_with_metric_aliases,
)
from evaluation.ollama_query_plan_adapter import build_instructions
from evaluation.ordered_comparator import compare_results_ordered


def single_row(**metrics):
    return {"columns": list(metrics), "rows": [dict(metrics)]}


REFERENCE = single_row(**{"[Gross Sales]": 1000.0, "[Units Sold]": 50})


# ------------------------------------------------------------------
# Multi-alias comparator
# ------------------------------------------------------------------

def test_two_renamed_metrics_pass():
    # Shape of 3B FRESH2-011: both metrics renamed, values identical.
    actual = single_row(**{"[Total Gross Sales]": 1000.0, "[Total Units Sold]": 50})
    assert not compare_results_with_metric_alias(REFERENCE, actual)["passed"]
    result = compare_results_with_metric_aliases(REFERENCE, actual)
    assert result["passed"]
    assert result["reason"] == "Results equivalent (2 metric aliases differ)"
    assert result["alias_mapping"] == {
        "[Total Gross Sales]": "[Gross Sales]",
        "[Total Units Sold]": "[Units Sold]",
    }


def test_mapping_is_found_by_values_not_names():
    actual = single_row(**{"[A]": 50, "[B]": 1000.0})
    result = compare_results_with_metric_aliases(REFERENCE, actual)
    assert result["passed"]
    assert result["alias_mapping"] == {"[A]": "[Units Sold]", "[B]": "[Gross Sales]"}


def test_renamed_metrics_with_wrong_values_fail():
    actual = single_row(**{"[A]": 50, "[B]": 999.0})
    result = compare_results_with_metric_aliases(REFERENCE, actual)
    assert not result["passed"]
    assert result["reason"] == "Row value mismatch"


def test_single_rename_reason_unchanged():
    actual = single_row(**{"[GS]": 1000.0, "[Units Sold]": 50})
    assert compare_results_with_metric_aliases(REFERENCE, actual)["reason"] == (
        "Results equivalent (metric alias differs)")


def test_dimension_rename_rejected():
    expected = {"columns": ["DimStore[Region]", "[Net Sales]"],
                "rows": [{"DimStore[Region]": "Central", "[Net Sales]": 1.0}]}
    actual = {"columns": ["DimStore[City]", "[NS]"],
              "rows": [{"DimStore[City]": "Central", "[NS]": 1.0}]}
    assert compare_results_with_metric_aliases(expected, actual)["reason"] == (
        "Column mismatch")


def test_unequal_column_counts_rejected():
    actual = single_row(**{"[A]": 1000.0, "[B]": 50, "[C]": 1})
    assert compare_results_with_metric_aliases(REFERENCE, actual)["reason"] == (
        "Column mismatch")


def test_too_many_renames_rejected():
    names = [f"[M{i}]" for i in range(5)]
    expected = single_row(**{name: i for i, name in enumerate(names)})
    actual = single_row(**{f"[X{i}]": i for i in range(5)})
    assert compare_results_with_metric_aliases(expected, actual)["reason"] == (
        "Column mismatch")


def test_identical_results_still_pass():
    assert compare_results_with_metric_aliases(REFERENCE, REFERENCE)["passed"]


def test_ordered_comparator_accepts_multi_alias_base():
    expected = {"columns": ["DimStore[Region]", "[GP]", "[NS]"], "rows": [
        {"DimStore[Region]": "S", "[GP]": 1.0, "[NS]": 3.0},
        {"DimStore[Region]": "N", "[GP]": 2.0, "[NS]": 4.0}]}
    actual = {"columns": ["DimStore[Region]", "[a]", "[b]"], "rows": [
        {"DimStore[Region]": "S", "[a]": 1.0, "[b]": 3.0},
        {"DimStore[Region]": "N", "[a]": 2.0, "[b]": 4.0}]}
    assert not compare_results_ordered(expected, actual)["passed"]
    assert compare_results_ordered(
        expected, actual,
        base_comparator=compare_results_with_metric_aliases)["passed"]
    reversed_rows = dict(actual, rows=actual["rows"][::-1])
    assert compare_results_ordered(
        expected, reversed_rows,
        base_comparator=compare_results_with_metric_aliases)["reason"] == (
        "Row order mismatch")


# ------------------------------------------------------------------
# Comparator is fixed per case set
# ------------------------------------------------------------------

@pytest.mark.parametrize("set_name, expected", [
    ("fresh_v1", "compare_results_with_metric_alias"),
    ("fresh_v2", "compare_results_with_metric_alias"),
    ("fresh_v3", "compare_results_with_metric_aliases"),
])
def test_comparator_per_set(set_name, expected):
    assert fresh.comparators_for(set_name)[0].__name__ == expected


def test_runner_scores_renamed_metrics_per_set():
    case = {"id": "C", "category": "answerable", "requirement": {
        "metrics": [{"kind": "measure", "name": "Gross Sales"},
                    {"kind": "measure", "name": "Units Sold"}],
        "group_by": [], "limit": None}}
    plan = {"metrics": [
        {"alias": "Total Gross Sales",
         "expression": {"kind": "measure", "name": "Gross Sales"}},
        {"alias": "Total Units Sold",
         "expression": {"kind": "measure", "name": "Units Sold"}}],
        "group_by": [], "filters": [], "order_by": [], "limit": None}
    reference = dict(REFERENCE, status="success")

    def execute(dax):
        row = {"[Total Gross Sales]": 1000.0, "[Total Units Sold]": 50}
        return {"columns": list(row), "rows": [row], "row_count": 1}

    record = {"status": "valid_plan", "plan": plan}
    old = fresh.evaluate_case(case, record, reference, execute, "fresh_v2")
    new = fresh.evaluate_case(case, record, reference, execute, "fresh_v3")
    assert old["passed"] is False
    assert new["passed"] is True and new["strict_passed"] is True
    assert new["rows"] == [{"[Total Gross Sales]": 1000.0, "[Total Units Sold]": 50}]


def test_resolve_paths_v3_arm():
    paths = fresh.resolve_paths("fresh_v3", "v3")
    assert paths["plans"].name == "fresh_v3_prompt_v3_query_plans.json"
    assert paths["set"] == "fresh_v3"


# ------------------------------------------------------------------
# Prompt v3
# ------------------------------------------------------------------

def test_prompt_v3_extends_v2_with_conventions_only():
    v2 = build_instructions("v2")
    v3 = build_instructions("v3")
    assert "MODEL CONVENTIONS:" in v3
    assert "MODEL CONVENTIONS:" not in v2
    start = v3.index("MODEL CONVENTIONS:")
    end = v3.index("WHEN NOT TO PLAN:")
    assert v3[:start] + v3[end:] == v2


def test_prompt_v3_conventions_reference_real_model_objects():
    v3 = build_instructions("v3")
    for name in ("DimDate[YearMonth]", "FactSales[CustomerKey]",
                 "FactSales[ProductKey]", "FactSales[OrderID]",
                 "FactSales[StoreKey]"):
        assert name in v3
    from evaluation.query_plan_validator import load_model
    columns = {f"{t['Name']}[{c['Name']}]" for t in load_model()["Tables"]
               for c in t.get("Columns", [])}
    assert {"DimDate[YearMonth]", "DimDate[MonthName]", "FactSales[CustomerKey]",
            "FactSales[ProductKey]", "FactSales[OrderID]",
            "FactSales[StoreKey]"} <= columns
