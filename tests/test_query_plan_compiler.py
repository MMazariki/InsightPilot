import pytest

from evaluation.query_plan_compiler import compile_query_plan


def make_plan():
    return {
        "metrics": [{
            "alias": "Net Sales",
            "expression": {
                "kind": "measure",
                "name": "Net Sales"
            }
        }],
        "group_by": [],
        "filters": [],
        "order_by": [],
        "limit": None
    }


def test_single_metric_returns_one_table():
    dax = compile_query_plan(make_plan())
    assert dax.count("EVALUATE") == 1
    assert 'ROW(' in dax
    assert '"Net Sales", [Net Sales]' in dax


def test_multiple_measures_one_result():
    plan = make_plan()
    plan["metrics"].append({
        "alias": "Gross Profit",
        "expression": {
            "kind": "measure",
            "name": "Gross Profit"
        }
    })
    dax = compile_query_plan(plan)
    assert dax.count("EVALUATE") == 1
    assert '"Gross Profit", [Gross Profit]' in dax


def test_grouping_by_dimension():
    plan = make_plan()
    plan["group_by"] = [
        {"table": "DimStore", "column": "Region"}
    ]
    dax = compile_query_plan(plan)
    assert "SUMMARIZECOLUMNS(" in dax
    assert "'DimStore'[Region]" in dax


def test_column_filter():
    plan = make_plan()
    plan["group_by"] = [
        {"table": "DimCustomer", "column": "CustomerSegment"}
    ]
    plan["filters"] = [{
        "type": "column",
        "table": "FactSales",
        "column": "Channel",
        "operator": "eq",
        "value": "Delivery"
    }]
    dax = compile_query_plan(plan)
    assert "CALCULATETABLE(" in dax
    assert """'FactSales'[Channel] = "Delivery" """ .strip() in dax


def test_metric_threshold():
    plan = make_plan()
    plan["group_by"] = [
        {"table": "FactSales", "column": "Channel"}
    ]
    plan["filters"] = [{
        "type": "metric",
        "alias": "Net Sales",
        "operator": "gt",
        "value": 100000
    }]
    dax = compile_query_plan(plan)
    assert "FILTER(" in dax
    assert "[Net Sales] > 100000" in dax


def test_top_five_ranking():
    plan = make_plan()
    plan["group_by"] = [
        {"table": "DimProduct", "column": "Brand"}
    ]
    plan["order_by"] = [{
        "field": "Net Sales",
        "direction": "desc"
    }]
    plan["limit"] = 5
    dax = compile_query_plan(plan)
    assert "TOPN(" in dax
    assert "5," in dax
    assert "ORDER BY [Net Sales] DESC" in dax


def test_distinct_order_count():
    plan = make_plan()
    plan["metrics"][0]["expression"] = {
        "kind": "distinct_count",
        "table": "FactSales",
        "column": "OrderID"
    }
    dax = compile_query_plan(plan)
    assert "DISTINCTCOUNT('FactSales'[OrderID])" in dax


def test_average_sales_per_order():
    plan = make_plan()
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
    dax = compile_query_plan(plan)
    assert "DIVIDE([Net Sales], DISTINCTCOUNT('FactSales'[OrderID]))" in dax


def test_boolean_filter():
    plan = make_plan()
    plan["filters"] = [{
        "type": "column",
        "table": "DimDate",
        "column": "IsWeekend",
        "operator": "eq",
        "value": True
    }]
    dax = compile_query_plan(plan)
    assert "'DimDate'[IsWeekend] = TRUE()" in dax


def test_filter_string_escaping():
    plan = make_plan()
    plan["filters"] = [{
        "type": "column",
        "table": "FactSales",
        "column": "Channel",
        "operator": "eq",
        "value": 'In-"Store"'
    }]
    dax = compile_query_plan(plan)
    assert '"In-""Store"""' in dax


def test_limit_without_sorting_rejected():
    plan = make_plan()
    plan["limit"] = 5
    with pytest.raises(ValueError, match="requires explicit sorting"):
        compile_query_plan(plan)


def test_unknown_measure_rejected():
    plan = make_plan()
    plan["metrics"][0]["expression"]["name"] = "Fake Measure"
    with pytest.raises(ValueError, match="Unknown measure"):
        compile_query_plan(plan)


def test_alias_collision_rejected_before_compilation():
    plan = make_plan()
    plan["group_by"] = [{"table": "DimProduct", "column": "Brand"}]
    plan["metrics"].append({
        "alias": "Brand",
        "expression": {
            "kind": "distinct_count",
            "table": "DimProduct",
            "column": "Brand"
        }
    })
    with pytest.raises(ValueError, match="collides with grouping column"):
        compile_query_plan(plan)
