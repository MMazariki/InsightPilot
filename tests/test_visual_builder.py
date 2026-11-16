import json

import pytest

from src import agent_factory
from src import settings as app_settings
from src.structured_agent import StructuredPlanningAgent
from src.visual_builder import (
    REASON_FILTERED,
    REASON_NO_BREAKDOWN,
    REASON_NO_MEASURE,
    build_visual_request,
    measure_tables,
)


def plan(metrics=("Net Sales",), group_by=(("DimStore", "Region"),),
         filters=(), limit=None):
    return {
        "metrics": [
            m if isinstance(m, dict) else
            {"alias": m, "expression": {"kind": "measure", "name": m}}
            for m in metrics],
        "group_by": [{"table": t, "column": c} for t, c in group_by],
        "filters": list(filters), "order_by": [], "limit": limit,
    }


def test_measure_tables_come_from_the_model():
    tables = measure_tables()
    assert tables["Net Sales"] == "FactSales"
    assert tables["Variance"] == "FactTarget"


def test_single_measure_uses_the_shape_the_foundry_agent_produced():
    request, reason = build_visual_request(plan())
    assert reason is None
    assert request == {
        "visualType": "clusteredColumnChart",
        "category": {"table": "DimStore", "column": "Region"},
        "value": {"table": "FactSales", "measure": "Net Sales"},
    }


@pytest.mark.parametrize("table,column,expected", [
    ("DimDate", "MonthName", "lineChart"),
    ("DimDate", "YearMonth", "lineChart"),
    ("DimProduct", "Brand", "clusteredBarChart"),
    ("DimStore", "City", "clusteredBarChart"),
    ("FactSales", "Channel", "clusteredColumnChart"),
])
def test_chart_type_follows_the_breakdown(table, column, expected):
    request, _ = build_visual_request(plan(group_by=[(table, column)]))
    assert request["visualType"] == expected


def test_two_measures_make_a_combo_chart():
    request, _ = build_visual_request(plan(metrics=("Net Sales", "Margin %")))
    assert request == {
        "visualType": "lineClusteredColumnComboChart",
        "fields": [
            {"role": "Category",
             "target": {"table": "DimStore", "column": "Region"}},
            {"role": "Y", "target": {"table": "FactSales", "measure": "Net Sales"}},
            {"role": "Y2", "target": {"table": "FactSales", "measure": "Margin %"}},
        ],
    }


def test_variance_measure_resolves_to_its_own_table():
    request, _ = build_visual_request(plan(metrics=("Variance",)))
    assert request["value"] == {"table": "FactTarget", "measure": "Variance"}


@pytest.mark.parametrize("bad_plan,reason", [
    (plan(group_by=[]), REASON_NO_BREAKDOWN),
    (plan(group_by=[("DimStore", "Region"), ("FactSales", "Channel")]),
     REASON_NO_BREAKDOWN),
    (plan(filters=[{"type": "column", "table": "DimProduct",
                    "column": "Category", "operator": "eq", "value": "Food"}]),
     REASON_FILTERED),
    (plan(limit=5), REASON_FILTERED),
    (plan(metrics=[{"alias": "Customers", "expression": {
        "kind": "distinct_count", "table": "FactSales",
        "column": "CustomerKey"}}]), REASON_NO_MEASURE),
    (plan(metrics=("Not A Measure",)), REASON_NO_MEASURE),
])
def test_no_chart_when_it_would_not_match_the_answer(bad_plan, reason):
    assert build_visual_request(bad_plan) == (None, reason)


def test_computed_metric_is_skipped_but_measures_still_chart():
    mixed = plan(metrics=[
        {"alias": "Customers", "expression": {
            "kind": "distinct_count", "table": "FactSales",
            "column": "CustomerKey"}},
        "Net Sales",
    ])
    request, _ = build_visual_request(mixed)
    assert request["value"] == {"table": "FactSales", "measure": "Net Sales"}


# ---- In the agent ------------------------------------------------------

class FakePowerBI:
    def __init__(self, rows=None):
        self.rows = [{"DimStore[Region]": "Central", "[Net Sales]": 1.0}] \
            if rows is None else rows

    def execute_dax(self, dax):
        return {"columns": list(self.rows[0]) if self.rows else [],
                "rows": self.rows, "row_count": len(self.rows)}

    def close(self):
        pass


def agent_for(reply, options=None, powerbi=None):
    return StructuredPlanningAgent(
        model="m", powerbi=powerbi or FakePowerBI(),
        generate=lambda model, question, instructions: json.dumps(reply),
        chat=lambda model, messages: json.dumps({"faithful": True, "reason": ""}),
        options=options)


def test_agent_does_not_chart_unless_asked():
    agent = agent_for(plan())
    answer = agent.ask("Net sales by region")
    assert agent.pending_visual_request is None
    assert "chart" not in answer.lower()


def test_agent_sets_visual_request_and_says_so():
    agent = agent_for(plan(), options={"create_visuals": True})
    answer = agent.ask("Net sales by region")
    assert agent.pending_visual_request == {
        "visualType": "clusteredColumnChart",
        "category": {"table": "DimStore", "column": "Region"},
        "value": {"table": "FactSales", "measure": "Net Sales"},
    }
    assert "A chart of this answer was added" in answer
    assert agent.last_trace["chart"] == "created"
    # The note sits with the table, before the collapsible details.
    assert answer.index("A chart of this answer") < answer.index(
        "**Query plan and DAX**")


def test_agent_clears_the_previous_chart_request():
    agent = agent_for(plan(), options={"create_visuals": True})
    agent.ask("Net sales by region")
    assert agent.pending_visual_request is not None
    agent.generate = lambda model, question, instructions: json.dumps(
        plan(group_by=[]))
    agent.ask("Total net sales")
    assert agent.pending_visual_request is None


def test_agent_explains_when_a_filtered_answer_gets_no_chart():
    filtered = plan(limit=3)
    filtered["order_by"] = [{"field": "Net Sales", "direction": "desc"}]
    agent = agent_for(filtered, options={"create_visuals": True})
    answer = agent.ask("Top 3 regions by net sales")
    assert agent.pending_visual_request is None
    assert "No chart was added" in answer


def test_agent_makes_no_chart_for_an_empty_result():
    agent = agent_for(plan(), options={"create_visuals": True},
                      powerbi=FakePowerBI(rows=[]))
    agent.ask("Net sales by region")
    assert agent.pending_visual_request is None


def test_app_settings_turn_charts_on_by_default_and_can_turn_them_off():
    settings = app_settings.defaults()
    kwargs = agent_factory.structured_agent_kwargs(
        settings, secrets={}, powerbi=FakePowerBI())
    assert kwargs["options"]["create_visuals"] is True

    settings["interface"]["create_charts"] = False
    kwargs = agent_factory.structured_agent_kwargs(
        settings, secrets={}, powerbi=FakePowerBI())
    assert kwargs["options"]["create_visuals"] is False
