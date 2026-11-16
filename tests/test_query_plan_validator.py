import copy

import pytest

from evaluation.query_plan_validator import validate_query_plan


def valid_plan():
    return {
        "metrics": [
            {
                "alias": "Net Sales",
                "expression": {
                    "kind": "measure",
                    "name": "Net Sales"
                }
            }
        ],
        "group_by": [
            {"table": "DimStore", "column": "Region"}
        ],
        "filters": [],
        "order_by": [
            {"field": "Net Sales", "direction": "desc"}
        ],
        "limit": 5
    }


def test_valid_plan():
    plan = valid_plan()
    assert validate_query_plan(plan) == plan


def test_unknown_measure():
    plan = valid_plan()
    plan["metrics"][0]["expression"]["name"] = "Imaginary Sales"

    with pytest.raises(ValueError, match="Unknown measure"):
        validate_query_plan(plan)


def test_unknown_table():
    plan = valid_plan()
    plan["group_by"][0]["table"] = "FakeTable"

    with pytest.raises(ValueError, match="Unknown table"):
        validate_query_plan(plan)


def test_unknown_column():
    plan = valid_plan()
    plan["group_by"][0]["column"] = "FakeRegion"

    with pytest.raises(ValueError, match="Unknown column"):
        validate_query_plan(plan)


def test_duplicate_metric_alias():
    plan = valid_plan()
    plan["metrics"].append(copy.deepcopy(plan["metrics"][0]))

    with pytest.raises(ValueError, match="Duplicate metric alias"):
        validate_query_plan(plan)


def test_invalid_sorting_field():
    plan = valid_plan()
    plan["order_by"][0]["field"] = "Unknown Metric"

    with pytest.raises(ValueError, match="Invalid sorting field"):
        validate_query_plan(plan)


def test_invalid_filter_type():
    plan = valid_plan()
    plan["filters"] = [{
        "type": "column",
        "table": "DimDate",
        "column": "Year",
        "operator": "eq",
        "value": "2025"
    }]

    with pytest.raises(ValueError, match="Invalid filter"):
        validate_query_plan(plan)


def test_valid_boolean_filter():
    plan = valid_plan()
    plan["filters"] = [{
        "type": "column",
        "table": "DimDate",
        "column": "IsWeekend",
        "operator": "eq",
        "value": True
    }]

    assert validate_query_plan(plan) == plan


def test_valid_distinct_count():
    plan = valid_plan()
    plan["metrics"][0]["expression"] = {
        "kind": "distinct_count",
        "table": "FactSales",
        "column": "OrderID"
    }

    assert validate_query_plan(plan) == plan


def test_valid_calculated_ratio():
    plan = valid_plan()
    plan["metrics"][0]["expression"] = {
        "kind": "divide",
        "numerator": {
            "kind": "measure",
            "name": "Net Sales"
        },
        "denominator": {
            "kind": "distinct_count",
            "table": "FactSales",
            "column": "OrderID"
        }
    }

    assert validate_query_plan(plan) == plan


def test_missing_required_property():
    plan = valid_plan()
    del plan["metrics"]

    with pytest.raises(ValueError, match="Invalid query plan"):
        validate_query_plan(plan)


def test_unsafe_metric_alias():
    plan = valid_plan()
    plan["metrics"][0]["alias"] = 'Sales" & EVALUATE'

    with pytest.raises(ValueError, match="Unsafe or invalid"):
        validate_query_plan(plan)


def test_duplicate_metric_alias_is_case_insensitive():
    plan = valid_plan()
    duplicate = copy.deepcopy(plan["metrics"][0])
    duplicate["alias"] = "net sales"
    plan["metrics"].append(duplicate)

    with pytest.raises(ValueError, match="Duplicate metric alias"):
        validate_query_plan(plan)


@pytest.mark.parametrize("alias", ["Region", "REGION"])
def test_metric_alias_colliding_with_grouping_column(alias):
    # Shape of the Qwen 7B HOLD-005 plan, which Power BI rejected with
    # "SUMMARIZECOLUMNS cannot add column [Brand] since it already exists".
    plan = valid_plan()
    plan["metrics"].append({
        "alias": alias,
        "expression": {
            "kind": "distinct_count",
            "table": "DimStore",
            "column": "Region"
        }
    })

    with pytest.raises(ValueError, match="collides with grouping column"):
        validate_query_plan(plan)


def test_metric_alias_matching_measure_name_is_allowed():
    # "Net Sales" alias over [Net Sales] is the reference convention.
    plan = valid_plan()
    assert plan["metrics"][0]["alias"] == "Net Sales"
    assert validate_query_plan(plan) == plan
