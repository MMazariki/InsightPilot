"""The fixes for the five app_cases_v2 failures (docs/EVALUATION.md §3.7),
checked without a model or Power BI."""

import datetime as dt

import pytest

from evaluation.request_guard import screen_request
from evaluation.semantic_checks import check_semantic_rules
from src import analysis
from src.interpret import interpret

TODAY = dt.date(2026, 11, 19)


def _plan(filters, group_by=None, metrics=None):
    return {"metrics": metrics or [{"alias": "Units Sold", "expression": {
        "kind": "measure", "name": "Units Sold"}}],
        "group_by": group_by or [], "filters": filters, "order_by": [], "limit": None}


def test_two_compared_years_are_not_a_single_value():
    years = [{"type": "column", "table": "DimDate", "column": "Year", "operator": "eq",
              "value": y} for y in (2021, 2024)]
    grouped = _plan(years, [{"table": "DimDate", "column": "Year"}])
    check_semantic_rules(grouped, reject_single_value_grouping=True)
    with pytest.raises(ValueError, match="single value"):
        check_semantic_rules(_plan(years[:1], [{"table": "DimDate", "column": "Year"}]),
                             reject_single_value_grouping=True)


@pytest.mark.parametrize("question", [
    "Update the sales target for Riyadh Mall 1 to 50000",
    "Increase all sales targets by 10%",
    "Please decrease the target for Western stores",
    "Set net sales for Riyadh Mall 2 to zero",
])
def test_write_requests_are_refused(question):
    assert screen_request(question)["clarification"]["reason"] == "write_request"


@pytest.mark.parametrize("question", [
    "How did net sales change between 2023 and 2026?",
    "Increase in net sales by region",
    "Which stores had the biggest decrease in units sold?",
])
def test_questions_with_change_words_are_not_refused(question):
    assert screen_request(question) is None


def test_typo_fixer_leaves_write_verbs_alone():
    text, _ = interpret("Update the sales target for Riyadh Mall 1 to 50000", TODAY)
    assert text.lower().startswith("update")


@pytest.mark.parametrize("question,expected", [
    ("Net sales on weekends vs weekdays", "Net sales by is weekend"),
    ("Units sold on weekdays and weekends", "Units sold by is weekend"),
    ("Weekend versus weekday gross profit", "Gross profit by is weekend"),
])
def test_weekends_against_weekdays_is_the_weekend_flag(question, expected):
    assert interpret(question, TODAY)[0] == expected


def test_sales_by_weekday_is_still_the_day_name():
    assert "day name" in interpret("Net sales by weekday", TODAY)[0]


def test_counting_customers_means_customers_with_sales():
    text, notes = interpret("How many customers bought something in 2025?", TODAY)
    assert text.startswith("Number of distinct customers") and "2025" in text and notes


def test_share_of_a_distinct_count_uses_counted_totals():
    plan = {"metrics": [{"alias": "Orders", "expression": {
        "kind": "distinct_count", "table": "FactSales", "column": "OrderID"}}],
        "group_by": [{"table": "DimDate", "column": "Year"},
                     {"table": "FactSales", "column": "Channel"}],
        "filters": [], "order_by": [], "limit": None}
    spec = {"share": {"value": "In-Store", "table": "FactSales", "column": "Channel"}}
    assert analysis.needs_counted_totals(plan)
    outer = analysis.totals_plan(plan, spec)
    assert outer["group_by"] == [{"table": "DimDate", "column": "Year"}]
    result = {"columns": ["DimDate[Year]", "FactSales[Channel]", "[Orders]"],
              "rows": [{"DimDate[Year]": 2025, "FactSales[Channel]": "In-Store", "[Orders]": 60},
                       {"DimDate[Year]": 2025, "FactSales[Channel]": "Delivery", "[Orders]": 50}],
              "row_count": 2}
    # 100 distinct orders in 2025 (10 have lines in both channels), not 110.
    totals = analysis.totals_from({"columns": ["DimDate[Year]", "[Orders]"],
                                   "rows": [{"DimDate[Year]": 2025, "[Orders]": 100}]}, outer)
    table, _ = analysis.finish(result, plan, spec, totals)
    assert [r["Share %"] for r in table["rows"]] == ["60.0%"]
    # Net sales add up: no counted totals needed.
    assert not analysis.needs_counted_totals({"metrics": [{"alias": "N", "expression": {
        "kind": "measure", "name": "Net Sales"}}]})


@pytest.mark.parametrize("question", [
    "What is our market share in Saudi Arabia?",
    "How do we compare with our competitors?",
    "Industry average gross margin",
    "Market size for snacks in Riyadh",
])
def test_outside_data_is_declined(question):
    from evaluation.request_guard import screen_outside_data

    assert screen_outside_data(question)["clarification"]["reason"] == "out_of_scope"


@pytest.mark.parametrize("question", [
    "Snacks share of net sales by region",
    "Share of units sold by store type",
    "Net sales by supermarket store type",
])
def test_own_shares_are_not_outside_data(question):
    from evaluation.request_guard import screen_outside_data

    assert screen_outside_data(question) is None


def test_market_is_not_corrected_to_make():
    assert "market" in interpret("What is our market share?", TODAY)[0].lower()
