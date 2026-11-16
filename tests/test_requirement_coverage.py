import copy
import json
from pathlib import Path

import pytest

from evaluation import run_requirement_coverage_evaluation as runner
from evaluation.query_plan_validator import validate_query_plan
from evaluation.requirement_coverage import (
    assert_requirement_coverage,
    check_requirement_coverage,
    metric_signature,
)

ROOT = Path(__file__).resolve().parents[1]
NET_SALES = {"kind": "measure", "name": "Net Sales"}


def top_brands_requirement():
    return {
        "id": "T-1",
        "metrics": [NET_SALES],
        "group_by": ["DimProduct[Brand]"],
        "column_filters": [],
        "metric_filters": [],
        "order_by": [{"metric": NET_SALES, "direction": "desc"}],
        "limit": 5,
    }


def top_brands_plan():
    return {
        "metrics": [{"alias": "Net Sales", "expression": dict(NET_SALES)}],
        "group_by": [{"table": "DimProduct", "column": "Brand"}],
        "filters": [],
        "order_by": [{"field": "Net Sales", "direction": "desc"}],
        "limit": 5,
    }


def codes(result):
    return sorted(item["code"] for item in result["findings"])


# ------------------------------------------------------------------
# Core coverage behaviour
# ------------------------------------------------------------------

def test_matching_plan_is_covered():
    result = check_requirement_coverage(top_brands_plan(), top_brands_requirement())
    assert result == {"covered": True, "findings": []}


def test_metric_alias_does_not_matter():
    plan = top_brands_plan()
    plan["metrics"][0]["alias"] = "Revenue"
    plan["order_by"][0]["field"] = "Revenue"
    assert check_requirement_coverage(plan, top_brands_requirement())["covered"]


def test_extra_metric_detected():
    # Shape of the Qwen 7B HOLD-005 plan.
    plan = top_brands_plan()
    plan["metrics"].append({
        "alias": "Brand",
        "expression": {"kind": "distinct_count",
                       "table": "DimProduct", "column": "Brand"},
    })
    assert codes(check_requirement_coverage(plan, top_brands_requirement())) == [
        "extra_metric"]


def test_extra_metric_allowed_when_requirement_permits():
    plan = top_brands_plan()
    plan["metrics"].append({"alias": "GP", "expression": {
        "kind": "measure", "name": "Gross Profit"}})
    requirement = top_brands_requirement()
    requirement["allow_extra_metrics"] = True
    assert check_requirement_coverage(plan, requirement)["covered"]


def test_duplicate_metric_counts_as_extra():
    plan = top_brands_plan()
    plan["metrics"].append({"alias": "Net Sales 2", "expression": dict(NET_SALES)})
    assert "extra_metric" in codes(
        check_requirement_coverage(plan, top_brands_requirement()))


def test_missing_metric_detected():
    plan = top_brands_plan()
    plan["metrics"][0]["expression"] = {"kind": "measure", "name": "Gross Sales"}
    plan["order_by"] = []
    found = codes(check_requirement_coverage(plan, top_brands_requirement()))
    assert "missing_metric" in found
    assert "extra_metric" in found


def test_missing_and_extra_grouping_detected():
    # Shape of the HOLD-009 failures: MonthName instead of YearMonth, plus
    # an extra grouping by the filtered channel.
    requirement = {
        "metrics": [NET_SALES],
        "group_by": ["DimDate[YearMonth]"],
        "column_filters": [{"field": "FactSales[Channel]",
                            "operator": "eq", "value": "In-Store"}],
        "limit": None,
    }
    plan = {
        "metrics": [{"alias": "Net Sales", "expression": dict(NET_SALES)}],
        "group_by": [{"table": "DimDate", "column": "MonthName"},
                     {"table": "FactSales", "column": "Channel"}],
        "filters": [{"type": "column", "table": "FactSales", "column": "Channel",
                     "operator": "eq", "value": "In-Store"}],
        "order_by": [{"field": "DimDate[MonthName]", "direction": "asc"}],
        "limit": None,
    }
    assert codes(check_requirement_coverage(plan, requirement)) == [
        "extra_group_by", "extra_group_by", "missing_group_by"]


def test_distinct_count_semantics_distinguished_from_measure():
    distinct = {"kind": "distinct_count", "table": "FactSales", "column": "OrderID"}
    assert metric_signature(distinct) != metric_signature(
        {"kind": "measure", "name": "OrderID"})


def test_divide_operand_order_matters():
    numerator = dict(NET_SALES)
    denominator = {"kind": "distinct_count", "table": "FactSales", "column": "OrderID"}
    requirement = {"metrics": [{"kind": "divide", "numerator": numerator,
                                "denominator": denominator}],
                   "group_by": [], "limit": None}
    plan = {"metrics": [{"alias": "x", "expression": {
        "kind": "divide", "numerator": denominator, "denominator": numerator}}],
        "group_by": [], "filters": [], "order_by": [], "limit": None}
    assert codes(check_requirement_coverage(plan, requirement)) == [
        "extra_metric", "missing_metric"]


# ------------------------------------------------------------------
# Filters and thresholds
# ------------------------------------------------------------------

def channel_requirement(value="Delivery"):
    return {"metrics": [NET_SALES], "group_by": [],
            "column_filters": [{"field": "FactSales[Channel]",
                                "operator": "eq", "value": value}],
            "limit": None}


def channel_plan(filters):
    return {"metrics": [{"alias": "Net Sales", "expression": dict(NET_SALES)}],
            "group_by": [], "filters": filters, "order_by": [], "limit": None}


def channel_filter(value, operator="eq", column="Channel"):
    return {"type": "column", "table": "FactSales", "column": column,
            "operator": operator, "value": value}


def test_missing_filter_detected():
    assert codes(check_requirement_coverage(
        channel_plan([]), channel_requirement())) == ["missing_filter"]


def test_filter_value_mismatch_detected():
    assert codes(check_requirement_coverage(
        channel_plan([channel_filter("In-Store")]),
        channel_requirement())) == ["filter_mismatch"]


def test_filter_operator_mismatch_detected():
    assert codes(check_requirement_coverage(
        channel_plan([channel_filter("Delivery", operator="ne")]),
        channel_requirement())) == ["filter_mismatch"]


def test_extra_filter_detected():
    plan = channel_plan([channel_filter("Delivery"),
                         channel_filter("Cash", column="PaymentMethod")])
    assert codes(check_requirement_coverage(plan, channel_requirement())) == [
        "extra_filter"]


def test_boolean_filter_not_equal_to_integer():
    requirement = {"metrics": [NET_SALES], "group_by": [], "limit": None,
                   "column_filters": [{"field": "DimDate[IsWeekend]",
                                       "operator": "eq", "value": True}]}
    plan = channel_plan([{"type": "column", "table": "DimDate",
                          "column": "IsWeekend", "operator": "eq", "value": 1}])
    assert codes(check_requirement_coverage(plan, requirement)) == [
        "filter_mismatch"]


def threshold_requirement():
    return {"metrics": [NET_SALES], "group_by": ["FactSales[Channel]"],
            "metric_filters": [{"metric": NET_SALES, "operator": "gt",
                                "value": 100000}],
            "limit": None}


def threshold_plan(operator="gt", value=100000, alias="Net Sales"):
    return {"metrics": [{"alias": "Net Sales", "expression": dict(NET_SALES)}],
            "group_by": [{"table": "FactSales", "column": "Channel"}],
            "filters": [{"type": "metric", "alias": alias,
                         "operator": operator, "value": value}],
            "order_by": [], "limit": None}


def test_threshold_covered():
    assert check_requirement_coverage(
        threshold_plan(), threshold_requirement())["covered"]


@pytest.mark.parametrize("operator, value", [("gte", 100000), ("gt", 10000)])
def test_threshold_mismatch_detected(operator, value):
    assert codes(check_requirement_coverage(
        threshold_plan(operator, value), threshold_requirement())) == [
        "threshold_mismatch"]


def test_missing_threshold_detected():
    plan = threshold_plan()
    plan["filters"] = []
    assert codes(check_requirement_coverage(plan, threshold_requirement())) == [
        "missing_threshold"]


def test_unresolved_threshold_alias_detected():
    found = codes(check_requirement_coverage(
        threshold_plan(alias="Sales"), threshold_requirement()))
    assert "unresolved_metric_filter" in found
    assert "missing_threshold" in found


# ------------------------------------------------------------------
# Ordering and limits
# ------------------------------------------------------------------

def test_ranking_direction_mismatch_detected():
    plan = top_brands_plan()
    plan["order_by"][0]["direction"] = "asc"
    assert codes(check_requirement_coverage(plan, top_brands_requirement())) == [
        "order_direction_mismatch"]


def test_ranking_on_wrong_field_detected():
    plan = top_brands_plan()
    plan["order_by"] = [{"field": "DimProduct[Brand]", "direction": "desc"}]
    assert codes(check_requirement_coverage(plan, top_brands_requirement())) == [
        "order_field_mismatch"]


def test_missing_ranking_detected():
    plan = top_brands_plan()
    plan["order_by"] = []
    assert codes(check_requirement_coverage(plan, top_brands_requirement())) == [
        "missing_order"]


def test_trailing_tie_breaker_allowed():
    plan = top_brands_plan()
    plan["order_by"].append({"field": "DimProduct[Brand]", "direction": "asc"})
    assert check_requirement_coverage(plan, top_brands_requirement())["covered"]


@pytest.mark.parametrize("limit", [None, 10])
def test_limit_mismatch_detected(limit):
    plan = top_brands_plan()
    plan["limit"] = limit
    assert codes(check_requirement_coverage(plan, top_brands_requirement())) == [
        "limit_mismatch"]


def test_unexpected_limit_detected():
    requirement = top_brands_requirement()
    requirement["order_by"] = []
    requirement["limit"] = None
    assert codes(check_requirement_coverage(top_brands_plan(), requirement)) == [
        "unexpected_limit"]


def test_presentation_order_ignored_when_not_required():
    requirement = top_brands_requirement()
    requirement["order_by"] = []
    requirement["limit"] = None
    plan = top_brands_plan()
    plan["limit"] = None
    assert check_requirement_coverage(plan, requirement)["covered"]


# ------------------------------------------------------------------
# Missing plans, unsupported intent, assertion helper
# ------------------------------------------------------------------

def test_no_plan_not_covered():
    assert codes(check_requirement_coverage(None, top_brands_requirement())) == [
        "no_plan"]


def test_unsupported_intent_must_not_be_answered():
    requirement = {"supported": False}
    assert codes(check_requirement_coverage(top_brands_plan(), requirement)) == [
        "unsupported_intent_answered"]
    assert check_requirement_coverage(None, requirement)["covered"]


def test_unrecognised_expression_reported_not_raised():
    plan = top_brands_plan()
    plan["metrics"][0]["expression"] = {"kind": "sql", "text": "SELECT 1"}
    found = codes(check_requirement_coverage(plan, top_brands_requirement()))
    assert "unrecognised_metric" in found
    assert "missing_metric" in found


def test_assert_requirement_coverage():
    plan = top_brands_plan()
    assert assert_requirement_coverage(plan, top_brands_requirement()) is plan
    plan["limit"] = 10
    with pytest.raises(ValueError, match="Requirement coverage failed"):
        assert_requirement_coverage(plan, top_brands_requirement())


def test_inputs_not_mutated():
    plan = top_brands_plan()
    requirement = top_brands_requirement()
    before = (copy.deepcopy(plan), copy.deepcopy(requirement))
    check_requirement_coverage(plan, requirement)
    assert (plan, requirement) == before


# ------------------------------------------------------------------
# Holdout requirement annotations
# ------------------------------------------------------------------

def load_requirements():
    return json.loads(
        (ROOT / "evaluation" / "holdout_requirements.json")
        .read_text(encoding="utf-8-sig")
    )["cases"]


def canonical_plan(requirement):
    """Build the simplest plan that satisfies a requirement spec."""

    metrics = [
        {"alias": f"M{index}", "expression": copy.deepcopy(item)}
        for index, item in enumerate(requirement["metrics"])
    ]
    aliases = {
        json.dumps(item, sort_keys=True): f"M{index}"
        for index, item in enumerate(requirement["metrics"])
    }

    def alias_for(metric):
        return aliases[json.dumps(metric, sort_keys=True)]

    def split(field):
        table, column = field[:-1].split("[", 1)
        return {"table": table, "column": column}

    filters = [
        {"type": "column", **split(item["field"]),
         "operator": item["operator"], "value": item["value"]}
        for item in requirement["column_filters"]
    ] + [
        {"type": "metric", "alias": alias_for(item["metric"]),
         "operator": item["operator"], "value": item["value"]}
        for item in requirement["metric_filters"]
    ]
    order_by = [
        {"field": alias_for(item["metric"]) if "metric" in item else item["field"],
         "direction": item["direction"]}
        for item in requirement["order_by"]
    ]
    return {
        "metrics": metrics,
        "group_by": [split(field) for field in requirement["group_by"]],
        "filters": filters,
        "order_by": order_by,
        "limit": requirement["limit"],
    }


def test_requirements_cover_every_holdout_case():
    cases = json.loads(
        (ROOT / "evaluation" / "holdout_cases.json").read_text(encoding="utf-8-sig")
    )
    requirements = {item["id"]: item for item in load_requirements()}
    assert len(requirements) == len(load_requirements()) == 12
    assert set(requirements) == {case["id"] for case in cases}

    for case in cases:
        assert set(requirements[case["id"]]["group_by"]) == set(
            case["expected_dimensions"]), case["id"]


@pytest.mark.parametrize("requirement", load_requirements(),
                         ids=lambda item: item["id"])
def test_requirement_round_trips_through_validator(requirement):
    plan = canonical_plan(requirement)
    validate_query_plan(plan)
    assert check_requirement_coverage(plan, requirement) == {
        "covered": True, "findings": []}


# ------------------------------------------------------------------
# Offline runner
# ------------------------------------------------------------------

def test_runner_gate_requires_schema_validity_and_coverage():
    requirement = dict(top_brands_requirement(), id="T-1")
    bad_schema = top_brands_plan()
    bad_schema["metrics"][0]["expression"]["name"] = "Imaginary Sales"
    generations = [
        {"model": "m", "id": "T-1", "status": "valid_plan", "plan": top_brands_plan()},
        {"model": "n", "id": "T-1", "status": "error", "plan": bad_schema,
         "error": "Unknown measure"},
        {"model": "o", "id": "T-1", "status": "error", "plan": None},
    ]
    records = {r["model"]: r for r in runner.evaluate_records(generations, [requirement])}

    assert records["m"]["gate_pass"] is True
    assert records["n"]["schema_valid"] is False
    assert records["n"]["gate_pass"] is False
    assert records["o"]["gate_pass"] is False
    assert records["o"]["findings"][0]["code"] == "no_plan"


def test_runner_rejects_case_without_requirement():
    with pytest.raises(ValueError, match="No requirement spec"):
        runner.evaluate_records(
            [{"model": "m", "id": "X", "plan": top_brands_plan()}], [])


def test_runner_agreement_matrix():
    records = [
        {"model": "m", "id": "A", "gate_pass": True},
        {"model": "m", "id": "B", "gate_pass": False},
        {"model": "m", "id": "C", "gate_pass": True},
        {"model": "m", "id": "D", "gate_pass": False},
    ]
    report = {"scores": {"m": {"cases": [
        {"id": "A", "passed": True}, {"id": "B", "passed": False},
        {"id": "C", "passed": False}, {"id": "D", "passed": True},
    ]}}}
    summary = runner.agreement_with_execution(records, report)["m"]
    assert summary["agreement"] == 2
    assert summary["total"] == 4
    assert summary["gate_pass_exec_fail"] == ["C"]
    assert summary["gate_fail_exec_pass"] == ["D"]


def test_runner_refuses_to_overwrite_report(tmp_path, monkeypatch):
    existing = tmp_path / "requirement_coverage_evaluation_v1.json"
    existing.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(runner, "OUTPUT_FILE", existing)
    with pytest.raises(FileExistsError):
        runner.main()
    assert existing.read_text(encoding="utf-8") == "{}"
