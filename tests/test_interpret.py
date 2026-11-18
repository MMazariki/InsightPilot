import datetime as dt
import json

import pytest

from evaluation.query_plan_compiler import column_literal, compile_query_plan
from src.interpret import (
    interpret,
    normalize_plan,
    plan_to_question,
)
from src.structured_agent import StructuredPlanningAgent, format_result_table

TODAY = dt.date(2026, 11, 17)


@pytest.mark.parametrize("question,expected", [
    ("Earnings by region", "Net sales by region"),
    ("How much money did each branch make?",
     "How much net sales did each store make?"),
    ("Which shops perform best?", "Which stores have the highest net sales?"),
    ("How did we do each quarter?", "What were net sales each quarter?"),
    ("Sales by period", "Net sales by year and month"),
    ("Sales by day of week", "Net sales by day name"),
    ("How many pieces sold per brand", "How many units sold by brand"),
    ("Sales reveneu by regoin", "Net sales by region"),
    ("Profitability by region", "Gross margin % by region"),
    ("Gross profit margin by city", "Gross margin % by city"),
    ("Revenue per month this year", "Net sales by month 2026"),
    ("Sales for last month", "Net sales for October 2026"),
    ("Revenue this quarter by region", "Net sales Q4 2026 by region"),
    ("Units sold yesterday", "Units sold 2026-11-16"),
    ("Sales by sales channel", "Net sales by sales channel"),
    ("Average unit price by category", "Gross sales divided by units sold by category"),
    ("What is the price per unit by brand?",
     "What is gross sales divided by units sold by brand?"),
    ("Average selling price by region", "Net sales divided by units sold by region"),
])
def test_interpret_rewrites(question, expected):
    text, notes = interpret(question, TODAY)
    assert text == expected
    assert notes


@pytest.mark.parametrize("question", [
    "Net sales by region",
    "Gross profit by year",
    "Units sold by category",
    "Sales target by store",
    "How many sales transactions were there?",
])
def test_model_wording_is_left_alone(question):
    text, notes = interpret(question, TODAY)
    if question.startswith(("Net", "Gross", "Units", "Sales target", "How many")):
        assert text == question and notes == []
    else:
        assert "given" in text  # inflections are not "typos"


def test_last_month_in_january_is_december_last_year():
    text, _ = interpret("Sales last month", dt.date(2026, 1, 5))
    assert text == "Net sales December 2025"


def _plan(filters, metrics=None, group_by=None, order_by=None):
    return {
        "metrics": metrics or [{"alias": "Net Sales", "expression": {
            "kind": "measure", "name": "Net Sales"}}],
        "group_by": group_by or [],
        "filters": filters, "order_by": order_by or [], "limit": None,
    }


def test_compare_two_years_becomes_a_breakdown():
    plan = _plan(
        [{"type": "column", "table": "DimDate", "column": "Year",
          "operator": "eq", "value": 2025},
         {"type": "column", "table": "DimDate", "column": "Year",
          "operator": "eq", "value": 2026}],
        metrics=[{"alias": "Sales 2025", "expression": {"kind": "measure", "name": "Net Sales"}},
                 {"alias": "Sales 2026", "expression": {"kind": "measure", "name": "Net Sales"}}])
    fixed, notes = normalize_plan(plan)
    # Only the two years compared (APP1-014 showed all six).
    assert fixed["filters"] == [{"type": "column", "table": "DimDate", "column": "Year",
                                 "operator": "in", "value": [2025, 2026]}]
    assert fixed["group_by"] == [{"table": "DimDate", "column": "Year"}]
    assert fixed["metrics"] == [{"alias": "Net Sales", "expression": {
        "kind": "measure", "name": "Net Sales"}}]
    assert notes
    compile_query_plan(fixed)


@pytest.mark.parametrize("value,expected", [
    ("2026-10", [("Year", 2026), ("MonthNumber", 10)]),
    ("2026-Q4", [("Year", 2026), ("Quarter", "Q4")]),
    ("Q3 2025", [("Year", 2025), ("Quarter", "Q3")]),
])
def test_year_month_text_is_split(value, expected):
    plan = _plan([{"type": "column", "table": "DimDate", "column": "YearMonth",
                   "operator": "eq", "value": value}])
    fixed, _ = normalize_plan(plan)
    assert [(f["column"], f["value"]) for f in fixed["filters"]] == expected
    compile_query_plan(fixed)


def test_compare_two_years_keeps_only_those_years():
    plan = _plan([{"type": "column", "table": "DimDate", "column": "Year",
                   "operator": "eq", "value": 2021},
                  {"type": "column", "table": "DimDate", "column": "Year",
                   "operator": "eq", "value": 2024}])
    fixed, notes = normalize_plan(plan)
    assert fixed["group_by"] == [{"table": "DimDate", "column": "Year"}]
    assert fixed["filters"] == [{"type": "column", "table": "DimDate", "column": "Year",
                                 "operator": "in", "value": [2021, 2024]}]
    dax = compile_query_plan(fixed)
    assert "'DimDate'[Year] IN {2021, 2024}" in dax


def test_internal_in_filter_is_not_open_to_the_planner():
    from evaluation.query_plan_validator import validate_query_plan

    plan = _plan([{"type": "column", "table": "DimDate", "column": "Year",
                   "operator": "in", "value": [2025, 2026]}])
    with pytest.raises(ValueError):
        validate_query_plan(plan)                       # the planner's contract
    validate_query_plan(plan, allow_in=True)            # the app's own fixes
    with pytest.raises(ValueError):
        validate_query_plan(_plan([{"type": "column", "table": "DimDate",
                                    "column": "Year", "operator": "in",
                                    "value": ["2025"]}]), allow_in=True)


@pytest.mark.parametrize("question,dropped", [
    ("Units sold by category in 2025", True),
    ("Gross profit by brand", True),
    ("Units sold by category by month in 2025", False),
    ("Quarterly net sales", False),
    ("Net sales trend", False),
    ("Units sold by category in March 2025", False),
])
def test_unasked_month_grouping_is_dropped(question, dropped):
    plan = _plan([], group_by=[{"table": "DimProduct", "column": "Category"},
                               {"table": "DimDate", "column": "YearMonth"}],
                 order_by=[{"field": "DimDate[YearMonth]", "direction": "asc"}])
    fixed, notes = normalize_plan(plan, question)
    columns = [g["column"] for g in fixed["group_by"]]
    assert ("YearMonth" not in columns) == dropped
    if dropped:
        assert fixed["order_by"] == [] and any("not asked" in n for n in notes)


def test_discount_verbs_and_column_measures():
    from src.interpret import fix_column_measures

    text, notes = interpret("Discounts given per channel in 2024", TODAY)
    assert text == "Total discounts by channel in 2024" and notes
    assert interpret("Total discounts by region", TODAY)[0] == "Total discounts by region"
    raw = ('{"metrics": [{"alias": "Target", "expression": {"kind": "measure", '
           '"name": "SalesTargetSAR"}}], "group_by": [{"table": "FactTarget", '
           '"column": "SalesTargetSAR"}]}')
    fixed, found = fix_column_measures(raw)
    assert found == ["SalesTargetSAR"]
    assert '"name": "Sales Target"' in fixed and '"column": "SalesTargetSAR"' in fixed


def test_percent_measure_keeps_its_percent_sign():
    plan = _plan([], metrics=[{"alias": "Target Achievement", "expression": {
        "kind": "measure", "name": "Target Achievement %"}}],
        order_by=[{"field": "Target Achievement", "direction": "desc"}])
    fixed, _ = normalize_plan(plan)
    assert fixed["metrics"][0]["alias"] == "Target Achievement %"
    assert fixed["order_by"][0]["field"] == "Target Achievement %"
    # Already right, or not a percentage: unchanged.
    for alias, name in [("Target Achievement %", "Target Achievement %"),
                        ("Revenue", "Net Sales")]:
        same = _plan([], metrics=[{"alias": alias, "expression": {
            "kind": "measure", "name": name}}])
        assert normalize_plan(same)[0]["metrics"][0]["alias"] == alias


def test_date_text_becomes_a_dax_date():
    assert column_literal("DimDate", "Date", "2026-11-16") == "DATE(2026, 11, 16)"
    assert column_literal("DimDate", "YearMonth", "2026-10") == "DATE(2026, 10, 1)"
    assert column_literal("DimStore", "Region", "2026-10") == '"2026-10"'


def test_plan_to_question():
    plan = _plan([{"type": "column", "table": "DimDate", "column": "Year",
                   "operator": "eq", "value": 2026}],
                 group_by=[{"table": "DimStore", "column": "Region"}])
    assert plan_to_question(plan) == "Net sales by region in 2026"


def test_years_and_months_are_shown_plainly():
    table = format_result_table(
        ["DimDate[Year]", "DimDate[YearMonth]", "[Net Sales]"],
        [{"DimDate[Year]": 2026, "DimDate[YearMonth]": "2026-10-01T00:00:00",
          "[Net Sales]": 1234.5}])
    assert "| 2026 | 2026-10 | 1,234.50 |" in table


class _PowerBI:
    def __init__(self, rows):
        self.rows = rows

    def execute_dax(self, dax):
        return {"columns": ["[Units Sold]"], "rows": self.rows,
                "row_count": len(self.rows)}

    def close(self):
        pass


def _agent(plan, rows, chat_responses=()):
    outputs = [json.dumps(plan)]
    chats = list(chat_responses)
    return StructuredPlanningAgent(
        model="fake", powerbi=_PowerBI(rows),
        generate=lambda *_: outputs.pop(0),
        chat=lambda *_: chats.pop(0))


def test_answer_says_how_the_question_was_read():
    plan = _plan([], group_by=[{"table": "DimStore", "column": "StoreName"}])
    agent = _agent(plan, [{"[Units Sold]": 5}])
    answer = agent.ask("Income by branch")
    assert answer.startswith('_Read "Income" as net sales; "branch" as store._')


def test_blank_total_reads_as_no_data():
    plan = _plan([], metrics=[{"alias": "Units Sold", "expression": {
        "kind": "measure", "name": "Units Sold"}}])
    agent = _agent(plan, [{"[Units Sold]": None}])
    assert "No data matches" in agent.ask("Units sold today")


def test_declined_question_without_a_metric_offers_metrics():
    plan = _plan([], group_by=[{"table": "DimDate", "column": "Year"}])
    verdict = json.dumps({"faithful": False, "reason": "vague"})
    agent = _agent(plan, [], chat_responses=[verdict])
    answer = agent.ask("Show me everything by year")
    assert "Did you mean" in answer
    assert agent.suggestions[0] == "Show me everything by year: net sales"


def test_interpretation_can_be_switched_off():
    plan = _plan([], group_by=[{"table": "DimStore", "column": "StoreName"}])
    agent = _agent(plan, [{"[Units Sold]": 5}],
                   chat_responses=[json.dumps({"faithful": True, "reason": ""})])
    agent.options["interpret_wording"] = False
    assert not agent.ask("Income by branch").startswith("_Read")


@pytest.mark.parametrize("question,expected", [
    ("How many orders by channel?", "Number of distinct orders by channel?"),
    ("Orders by region", "Number of distinct orders by region"),
    ("Units per order by channel", "Units sold divided by orders by channel"),
])
def test_order_counts_are_read_as_distinct_orders(question, expected):
    assert interpret(question, TODAY)[0] == expected


def test_metric_labelled_orders_is_recounted():
    plan = _plan([], metrics=[{"alias": "Total Orders", "expression": {
        "kind": "measure", "name": "Units Sold"}}],
        group_by=[{"table": "FactSales", "column": "Channel"}])
    fixed, notes = normalize_plan(plan)
    assert fixed["metrics"][0]["expression"] == {
        "kind": "distinct_count", "table": "FactSales", "column": "OrderID"}
    assert notes
    ratio = _plan([], metrics=[{"alias": "Units per Order", "expression": {
        "kind": "measure", "name": "Units Sold"}}])
    assert normalize_plan(ratio)[0] == ratio


@pytest.mark.parametrize("question,expected", [
    ("Sales target vs net sales by region in 2025",
     "Sales target and net sales by region in 2025"),
    ("Which category missed its target the most in 2026?",
     "Which category had the lowest target achievement % in 2026?"),
    ("Which store beat its target by the most?",
     "Which store had the highest target achievement %?"),
    ("Net sales 2025 vs 2026", "Net sales 2025 vs 2026"),  # years: growth
])
def test_target_wording(question, expected):
    assert interpret(question, TODAY)[0] == expected


def test_percentage_measures_are_shown_as_percent():
    table = format_result_table(["DimDate[Year]", "[Target Achievement %]"],
                                [{"DimDate[Year]": 2025,
                                  "[Target Achievement %]": 0.9098}])
    assert "| 2025 | 91.0% |" in table


@pytest.mark.parametrize("question,expected", [
    ("Average order value by region", "Net sales divided by orders by region"),
    ("AOV by channel", "Net sales divided by orders by channel"),
    ("Basket size by channel", "Units sold divided by orders by channel"),
])
def test_per_order_wording(question, expected):
    assert interpret(question, TODAY)[0] == expected


def test_per_order_ratio_divides_by_orders():
    plan = _plan([], metrics=[{"alias": "Average Order Value", "expression": {
        "kind": "divide",
        "numerator": {"kind": "measure", "name": "Net Sales"},
        "denominator": {"kind": "measure", "name": "Units Sold"}}}])
    fixed, notes = normalize_plan(plan)
    assert fixed["metrics"][0]["expression"]["denominator"]["column"] == "OrderID"
    assert notes
    good = _plan([], metrics=[{"alias": "Average Order Value", "expression": {
        "kind": "divide",
        "numerator": {"kind": "measure", "name": "Net Sales"},
        "denominator": {"kind": "measure", "name": "Orders"}}}])
    assert normalize_plan(good)[0] == good


MONTH = {"table": "DimDate", "column": "MonthName"}


def test_month_without_a_year_becomes_year_month():
    plan = _plan([], group_by=[MONTH], order_by=[
        {"field": "DimDate[MonthName]", "direction": "asc"}])
    fixed, notes = normalize_plan(plan, "Net sales by month")
    assert fixed["group_by"] == [{"table": "DimDate", "column": "YearMonth"}]
    assert fixed["order_by"] == [{"field": "DimDate[YearMonth]", "direction": "asc"}]
    assert notes


@pytest.mark.parametrize("question", [
    "Net sales by month of the year", "Seasonality of units by month",
    "Net sales by month across all years",
])
def test_month_of_the_year_is_kept(question):
    plan = _plan([], group_by=[MONTH])
    assert normalize_plan(plan, question)[0] == plan


def test_month_with_a_year_is_kept():
    plan = _plan([{"type": "column", "table": "DimDate", "column": "Year",
                   "operator": "eq", "value": 2025}], group_by=[MONTH])
    assert normalize_plan(plan, "Net sales by month in 2025")[0] == plan


def test_month_names_sort_in_calendar_order_even_when_sorted_by_name():
    from src.structured_agent import _calendar_order
    rows = [{"DimDate[MonthName]": m, "[Net Sales]": 1} for m in
            ("April", "February", "January")]
    plan = _plan([], group_by=[MONTH], order_by=[
        {"field": "DimDate[MonthName]", "direction": "asc"}])
    result = _calendar_order({"columns": ["DimDate[MonthName]", "[Net Sales]"],
                              "rows": rows}, plan)
    assert [r["DimDate[MonthName]"] for r in result["rows"]] == [
        "January", "February", "April"]


def test_time_columns_sort_in_time_order_without_an_order():
    from src.structured_agent import _calendar_order
    rows = [{"DimDate[YearMonth]": m, "[Net Sales]": 1} for m in
            ("2025-01-01T00:00:00", "2021-03-01T00:00:00", "2021-01-01T00:00:00")]
    plan = _plan([], group_by=[{"table": "DimDate", "column": "YearMonth"}])
    result = _calendar_order({"columns": ["DimDate[YearMonth]", "[Net Sales]"],
                              "rows": rows}, plan)
    assert [r["DimDate[YearMonth]"][:7] for r in result["rows"]] == [
        "2021-01", "2021-03", "2025-01"]
    ranked = dict(plan, order_by=[{"field": "Net Sales", "direction": "desc"}])
    assert _calendar_order({"columns": ["DimDate[YearMonth]", "[Net Sales]"],
                            "rows": rows}, ranked)["rows"] == rows
