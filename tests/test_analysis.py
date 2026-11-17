import datetime as dt
import json

import pytest

from evaluation.query_plan_validator import validate_query_plan
from src.analysis import adjust_plan, detect, finish, period_range
from src.structured_agent import StructuredPlanningAgent

TODAY = dt.date(2026, 11, 17)


def _plan(group_by=(), filters=(), metric="Net Sales", alias=None, limit=None,
          order_by=()):
    return {"metrics": [{"alias": alias or metric,
                         "expression": {"kind": "measure", "name": metric}}],
            "group_by": [dict(g) for g in group_by], "filters": list(filters),
            "order_by": list(order_by), "limit": limit}


STORE = {"table": "DimStore", "column": "StoreName"}
YEAR = {"table": "DimDate", "column": "Year"}
CHANNEL = {"table": "FactSales", "column": "Channel"}


@pytest.mark.parametrize("question,cleaned,kind", [
    ("Which store grew the most between 2021 and 2026?", "Net sales by store", "growth"),
    ("Net sales growth year over year", "Net sales", "growth"),
    ("How did units sold change between 2023 and 2026 by category?",
     "Units sold by category", "growth"),
    ("Delivery share of net sales by year", "Net sales by year", "share"),
    ("Share of net sales by channel", "Net sales by channel", "share"),
    ("Net sales in Ramadan 2024", "Net sales", "period"),
])
def test_detect(question, cleaned, kind):
    text, spec, notes = detect(question, TODAY)
    assert text == cleaned
    assert kind in spec


@pytest.mark.parametrize("question", [
    "Net sales by region",
    "Change the Q4 sales target to 5 million.",  # left to the request guard
    "Units sold by category in 2026",
])
def test_ordinary_questions_are_left_alone(question):
    assert detect(question, TODAY)[1] is None


def test_declines_rank_the_other_way():
    _, most, _ = detect("Which region declined the most from 2024 to 2025?", TODAY)
    _, least, _ = detect("Which region declined the least from 2024 to 2025?", TODAY)
    assert most["growth"]["direction"] == "asc"
    assert least["growth"]["direction"] == "desc"


def test_periods():
    assert period_range("ramadan", 2024) == (dt.date(2024, 3, 11), dt.date(2024, 4, 9))
    assert period_range("eid", 2024) == (dt.date(2024, 4, 10), dt.date(2024, 4, 13))
    assert period_range("white friday", 2025) == (dt.date(2025, 11, 28),
                                                  dt.date(2025, 11, 30))
    _, spec, _ = detect("Sales during Ramadan", TODAY)
    assert spec["period"]["year"] == 2026  # latest Ramadan that has ended


def test_growth_plan_and_result():
    _, spec, _ = detect("Which store grew the most between 2021 and 2026?", TODAY)
    plan, _ = adjust_plan(_plan([STORE], limit=1, order_by=[
        {"field": "Net Sales", "direction": "desc"}]), spec)
    validate_query_plan(plan)
    assert plan["group_by"] == [STORE, YEAR]
    assert plan["limit"] is None and plan["order_by"] == []
    rows = [
        {"DimStore[StoreName]": "A", "DimDate[Year]": 2021, "[Net Sales]": 100.0},
        {"DimStore[StoreName]": "A", "DimDate[Year]": 2026, "[Net Sales]": 150.0},
        {"DimStore[StoreName]": "B", "DimDate[Year]": 2021, "[Net Sales]": 100.0},
        {"DimStore[StoreName]": "B", "DimDate[Year]": 2023, "[Net Sales]": 999.0},
        {"DimStore[StoreName]": "B", "DimDate[Year]": 2026, "[Net Sales]": 300.0},
    ]
    result = {"columns": ["DimStore[StoreName]", "DimDate[Year]", "[Net Sales]"],
              "rows": rows, "row_count": 5}
    table, _ = finish(result, plan, spec)
    assert table["columns"] == ["DimStore[StoreName]", "Net Sales 2021",
                                "Net Sales 2026", "Change", "Change %"]
    assert table["rows"] == [{"DimStore[StoreName]": "B", "Net Sales 2021": 100.0,
                              "Net Sales 2026": 300.0, "Change": 200.0,
                              "Change %": "200.0%"}]  # planner's limit 1 kept


def test_year_over_year():
    _, spec, _ = detect("Net sales growth year over year", TODAY)
    plan, _ = adjust_plan(_plan(), spec)
    rows = [{"DimDate[Year]": y, "[Net Sales]": v}
            for y, v in [(2021, 100.0), (2022, 110.0), (2023, 99.0)]]
    table, _ = finish({"columns": ["DimDate[Year]", "[Net Sales]"], "rows": rows,
                       "row_count": 3}, plan, spec)
    assert [r["Change %"] for r in table["rows"]] == ["", "10.0%", "-10.0%"]


def test_share_of_a_named_value():
    _, spec, _ = detect("Delivery share of net sales by year", TODAY)
    plan, _ = adjust_plan(_plan([YEAR], filters=[
        {"type": "column", "table": "FactSales", "column": "Channel",
         "operator": "eq", "value": "Delivery"}]), spec)
    validate_query_plan(plan)
    assert plan["group_by"] == [YEAR, CHANNEL] and plan["filters"] == []
    rows = [{"DimDate[Year]": 2025, "FactSales[Channel]": c, "[Net Sales]": v}
            for c, v in [("Delivery", 25.0), ("In-Store", 70.0), ("Click & Collect", 5.0)]]
    table, _ = finish({"columns": ["DimDate[Year]", "FactSales[Channel]", "[Net Sales]"],
                       "rows": rows, "row_count": 3}, plan, spec)
    assert [(r["FactSales[Channel]"], r["Share %"]) for r in table["rows"]] == [
        ("Delivery", "25.0%")]


def test_share_across_one_breakdown():
    _, spec, _ = detect("Share of net sales by channel", TODAY)
    plan, _ = adjust_plan(_plan([CHANNEL]), spec)
    rows = [{"FactSales[Channel]": c, "[Net Sales]": v}
            for c, v in [("Delivery", 1.0), ("In-Store", 3.0)]]
    table, _ = finish({"columns": ["FactSales[Channel]", "[Net Sales]"],
                       "rows": rows, "row_count": 2}, plan, spec)
    assert [r["Share %"] for r in table["rows"]] == ["75.0%", "25.0%"]


def test_period_replaces_planner_date_filters():
    _, spec, _ = detect("Net sales in Ramadan 2024", TODAY)
    plan, _ = adjust_plan(_plan(filters=[
        {"type": "column", "table": "DimDate", "column": "MonthName",
         "operator": "eq", "value": "March"}]), spec)
    validate_query_plan(plan)
    assert [(f["column"], f["operator"], f["value"]) for f in plan["filters"]] == [
        ("Date", "gte", "2024-03-11"), ("Date", "lte", "2024-04-09")]


class _PowerBI:
    def __init__(self, result):
        self.result = result
        self.queries = []

    def execute_dax(self, dax):
        self.queries.append(dax)
        return self.result

    def close(self):
        pass


def test_agent_answers_a_growth_question_end_to_end():
    planned = _plan([STORE])
    rows = [
        {"DimStore[StoreName]": "A", "DimDate[Year]": 2021, "[Net Sales]": 100.0},
        {"DimStore[StoreName]": "A", "DimDate[Year]": 2026, "[Net Sales]": 130.0},
    ]
    powerbi = _PowerBI({"columns": ["DimStore[StoreName]", "DimDate[Year]",
                                    "[Net Sales]"], "rows": rows, "row_count": 2})
    outputs = [json.dumps(planned)]
    agent = StructuredPlanningAgent(model="fake", powerbi=powerbi,
                                    generate=lambda *_: outputs.pop(0),
                                    chat=lambda *_: json.dumps({"faithful": True}))
    answer = agent.ask("Which store grew the most between 2021 and 2026?")
    assert "'DimDate'[Year] >= 2021" in powerbi.queries[0]
    assert "| A | 100.00 | 130.00 | 30.00 | 30.0% |" in answer
    assert "analysis" in agent.last_trace["stages"]


@pytest.mark.parametrize("question,blocked", [
    ("Which store grew the most between 2021 and 2026? Growth ranking with "
     "change and change % for each store", False),
    ("Show the change in sales by store", False),
    ("Change the Q4 sales target to 5 million.", True),
    ("Please change store 3 name to X", True),
])
def test_request_guard_reads_change_as_a_noun(question, blocked):
    from evaluation.request_guard import screen_request
    assert bool(screen_request(question)) is blocked
