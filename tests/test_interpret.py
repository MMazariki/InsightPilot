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
])
def test_interpret_rewrites(question, expected):
    text, notes = interpret(question, TODAY)
    assert text == expected
    assert notes


@pytest.mark.parametrize("question", [
    "Net sales by region",
    "Gross profit by year",
    "Discounts given per order",
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
    assert fixed["filters"] == []
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
