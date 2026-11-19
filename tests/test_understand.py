"""Step 1: the AI reads the question as plain English (src/understand.py)."""

import datetime as dt
import json

import pytest

from src import understand as und
from src.structured_agent import StructuredPlanningAgent

pytestmark = pytest.mark.understand
TODAY = dt.date(2026, 11, 19)

PLAN = {"metrics": [{"alias": "Cost", "expression": {"kind": "measure", "name": "Cost"}}],
        "group_by": [{"table": "DimDate", "column": "Year"}],
        "filters": [{"type": "column", "table": "DimDate", "column": "Year",
                     "operator": "gte", "value": 2025}],
        "order_by": [], "limit": None}


def reading(**fields):
    base = {"request": "Cost by Year, for Year 2025 and 2026",
            "assumptions": [], "chart": None, "growth": None, "share": None,
            "holiday": None, "out_of_scope": False}
    return json.dumps({**base, **fields})


class FakePowerBI:
    def __init__(self):
        self.queries = []

    def execute_dax(self, dax):
        self.queries.append(dax)
        return {"columns": ["DimDate[Year]", "[Cost]"],
                "rows": [{"DimDate[Year]": 2025, "[Cost]": 1.0},
                         {"DimDate[Year]": 2026, "[Cost]": 2.0}],
                "row_count": 2}


def agent(read_reply, plan_replies, options=None):
    seen = {"planned": [], "read": []}
    replies = list(plan_replies)

    def generate(model, question, instructions):
        seen["planned"].append(question)
        return replies.pop(0)

    def chat(model, messages):
        if "first step of a business-intelligence" in messages[0]["content"]:
            seen["read"].append(messages[-1]["content"])
            return read_reply
        return replies.pop(0)

    a = StructuredPlanningAgent(powerbi=FakePowerBI(), generate=generate,
                                chat=chat, options=options)
    return a, seen


def test_prompt_gives_today_and_the_model_names():
    text = und.instructions(TODAY)
    assert "2026-11-19" in text and "Thursday" in text
    assert "Gross Profit" in text and "ProductName" in text
    assert "Never refuse" in text


def test_the_planner_gets_the_explicit_request_not_the_raw_wording():
    a, seen = agent(reading(assumptions=["the last two years as 2025 and 2026"]),
                    [json.dumps(PLAN)])
    answer = a.ask("cost by yr for the last two years")
    assert seen["read"] == ["cost by yr for the last two years"]
    assert seen["planned"] == ["Cost by Year, for Year 2025 and 2026"]
    assert "the last two years as 2025 and 2026" in answer
    assert a.last_result["kind"] == "answer"
    assert a.last_trace["stages"][:2] == ["understood", "planner"]


def test_planner_clarification_is_retried_as_a_plan():
    clar = json.dumps({"clarification": {"reason": "ambiguous",
                                         "message": "Which measure?"}})
    a, _ = agent(reading(), [clar, json.dumps(PLAN)])
    a.ask("how are we doing lately")
    assert a.last_result["kind"] == "answer"
    assert "plan_instead_of_clarification" in a.last_trace["stages"]


def test_unrelated_message_is_the_only_decline():
    a, seen = agent(reading(request="", out_of_scope=True), [])
    answer = a.ask("what's the weather tomorrow")
    assert a.last_result == {"kind": "declined", "reason": "out_of_scope"}
    assert seen["planned"] == [] and a.powerbi.queries == []
    assert "No query was executed" in answer


def test_requests_to_change_data_are_still_refused_before_reading():
    a, seen = agent(reading(), [])
    a.ask("Delete all sales for 2024")
    assert seen["read"] == [] and a.powerbi.queries == []
    assert a.last_result["kind"] == "declined"


def test_unreadable_reading_falls_back_to_planning_the_question():
    a, seen = agent("not json at all", [json.dumps(PLAN)])
    a.ask("Cost by year")
    assert "understand_failed" in a.last_trace["stages"]
    assert a.last_result["kind"] == "answer"


def test_structured_fields_become_the_analysis_spec():
    spec, notes = und.to_spec({
        "growth": {"mode": "range", "years": ["2021", 2026], "direction": "asc"},
        "holiday": {"name": "Ramadan", "year": 2024}}, TODAY)
    assert spec["growth"] == {"mode": "range", "years": [2021, 2026],
                              "direction": "asc"}
    assert spec["period"]["start"] == "2024-03-11"
    assert any("Ramadan 2024" in n for n in notes)


def test_share_value_is_matched_to_its_column():
    spec, _ = und.to_spec({"share": {"value": "delivery"}}, TODAY)
    assert spec["share"]["column"] == "Channel"
    assert spec["share"]["value"] == "Delivery"


def test_chart_word_maps_to_a_visual_type():
    reply = und.understand("x", lambda m, msgs: reading(chart="Line"), "m", TODAY)
    assert reply["chart"] == "lineChart"


def test_compose_writes_explicit_years_dates_filters_and_top():
    text = und.compose({
        "measures": ["Cost", "Gross Profit"],
        "breakdowns": ["DimProduct[ProductName]", "DimDate[Year]"],
        "filters": [{"column": "FactSales[Channel]", "values": ["Delivery"]}],
        "years": [2026, 2025], "sort": {"by": "Cost", "direction": "desc"},
        "top": 5})
    assert text == ('Cost and Gross Profit by DimProduct[ProductName] and '
                    'DimDate[Year], where FactSales[Channel] is "Delivery" and '
                    'DimDate[Year] from 2025 to 2026, top 5 by Cost (descending)')
    assert und.readable(text).startswith("Cost and Gross Profit by product name and year")


def test_compose_leaves_ranking_of_growth_to_the_analysis():
    text = und.compose({"measures": ["Net Sales"], "breakdowns": ["DimStore[StoreName]"],
                        "sort": {"by": {"x": 1}}, "top": 3,
                        "growth": {"mode": "range", "years": [2023, 2025]}})
    assert text == "Net Sales by DimStore[StoreName]"
