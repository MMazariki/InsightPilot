import json

import pytest

from src import agent_factory
from src import settings as app_settings
from src.structured_agent import StructuredPlanningAgent
from src.visual_builder import (
    NOTE_LIMIT,
    NOTE_METRIC_FILTER,
    NOTE_RATIO,
    REASON_NO_METRIC,
    build_visual_request,
    measure_tables,
)

NET = {"table": "FactSales", "measure": "Net Sales"}
REGION = {"table": "DimStore", "column": "Region"}
CUSTOMERS = {"alias": "Customers", "expression": {
    "kind": "distinct_count", "table": "FactSales", "column": "CustomerKey"}}
RATIO = {"alias": "Units per order", "expression": {
    "kind": "divide",
    "numerator": {"kind": "measure", "name": "Units Sold"},
    "denominator": {"kind": "distinct_count", "table": "FactSales",
                    "column": "OrderID"}}}


def plan(metrics=("Net Sales",), group_by=(("DimStore", "Region"),),
         filters=(), limit=None, order_by=()):
    return {
        "metrics": [
            m if isinstance(m, dict) else
            {"alias": m, "expression": {"kind": "measure", "name": m}}
            for m in metrics],
        "group_by": [{"table": t, "column": c} for t, c in group_by],
        "filters": list(filters), "order_by": list(order_by), "limit": limit,
    }


def column_filter(table, column, operator, value):
    return {"type": "column", "table": table, "column": column,
            "operator": operator, "value": value}


def test_measure_tables_come_from_the_model():
    tables = measure_tables()
    assert tables["Net Sales"] == "FactSales"
    assert tables["Variance"] == "FactTarget"


# ---- Shape -------------------------------------------------------------

def test_one_breakdown_one_measure():
    request, notes = build_visual_request(plan(), "Net sales by region")
    assert notes == []
    assert request == {
        "visualType": "clusteredColumnChart",
        "fields": [{"role": "Category", "target": REGION},
                   {"role": "Y", "target": NET}],
        "title": "Net sales by region",
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
    assert request["visualType"] == "lineClusteredColumnComboChart"
    assert [f["role"] for f in request["fields"]] == ["Category", "Y", "Y2"]
    assert request["fields"][2]["target"] == {
        "table": "FactSales", "measure": "Margin %"}


def test_a_single_total_becomes_a_card():
    request, notes = build_visual_request(plan(group_by=[]))
    assert notes == []
    assert request == {"visualType": "card",
                       "fields": [{"role": "Values", "target": NET}]}
    request, _ = build_visual_request(
        plan(metrics=("Net Sales", "Gross Profit"), group_by=[]))
    assert request["visualType"] == "multiRowCard"
    assert len(request["fields"]) == 2


def test_two_breakdowns_use_the_second_as_the_legend():
    request, _ = build_visual_request(
        plan(group_by=[("DimStore", "Region"), ("FactSales", "Channel")]))
    assert request["visualType"] == "clusteredColumnChart"
    assert request["fields"] == [
        {"role": "Category", "target": REGION},
        {"role": "Series", "target": {"table": "FactSales", "column": "Channel"}},
        {"role": "Y", "target": NET},
    ]


def test_time_goes_on_the_axis_of_a_two_way_breakdown():
    request, _ = build_visual_request(
        plan(group_by=[("FactSales", "Channel"), ("DimDate", "YearMonth")]))
    assert request["visualType"] == "lineChart"
    assert request["fields"][0]["target"]["column"] == "YearMonth"
    assert request["fields"][1] == {
        "role": "Series", "target": {"table": "FactSales", "column": "Channel"}}


def test_three_breakdowns_become_a_table():
    request, _ = build_visual_request(plan(group_by=[
        ("DimStore", "Region"), ("FactSales", "Channel"), ("DimDate", "Year")]))
    assert request["visualType"] == "tableEx"
    assert [f["role"] for f in request["fields"]] == ["Values"] * 4


# ---- Metrics -----------------------------------------------------------

def test_distinct_count_is_bound_as_a_column_aggregation():
    request, notes = build_visual_request(plan(metrics=[CUSTOMERS]))
    assert notes == []
    assert request["fields"][1]["target"] == {
        "table": "FactSales", "column": "CustomerKey",
        "aggregationFunction": "Count"}


def test_a_ratio_is_left_out_and_reported():
    request, notes = build_visual_request(plan(metrics=["Net Sales", RATIO]))
    assert notes == [NOTE_RATIO]
    assert [f["role"] for f in request["fields"]] == ["Category", "Y"]


def test_no_visual_when_nothing_can_be_bound():
    assert build_visual_request(plan(metrics=[RATIO])) == (None, [REASON_NO_METRIC])
    assert build_visual_request(plan(metrics=("Not A Measure",))) == (
        None, [REASON_NO_METRIC])


def test_variance_measure_resolves_to_its_own_table():
    request, _ = build_visual_request(plan(metrics=("Variance",)))
    assert request["fields"][1]["target"] == {
        "table": "FactTarget", "measure": "Variance"}


# ---- Filters -----------------------------------------------------------

def test_column_filters_are_passed_to_the_visual():
    request, notes = build_visual_request(plan(filters=[
        column_filter("DimProduct", "Category", "eq", "Food"),
        column_filter("FactSales", "Channel", "ne", "Delivery"),
        column_filter("DimDate", "Year", "gte", 2026),
    ]))
    assert notes == []
    assert request["filters"] == [
        {"kind": "basic", "target": {"table": "DimProduct", "column": "Category"},
         "operator": "In", "values": ["Food"]},
        {"kind": "basic", "target": {"table": "FactSales", "column": "Channel"},
         "operator": "NotIn", "values": ["Delivery"]},
        {"kind": "advanced", "target": {"table": "DimDate", "column": "Year"},
         "operator": "GreaterThanOrEqual", "value": 2026},
    ]


def test_metric_filter_uses_the_measure():
    request, notes = build_visual_request(plan(filters=[
        {"type": "metric", "alias": "Net Sales", "operator": "gt", "value": 5000}]))
    assert notes == []
    assert request["filters"] == [
        {"kind": "advanced", "target": NET, "operator": "GreaterThan",
         "value": 5000}]


def test_metric_filter_on_a_ratio_is_reported_not_applied():
    request, notes = build_visual_request(plan(
        metrics=["Net Sales", RATIO],
        filters=[{"type": "metric", "alias": "Units per order",
                  "operator": "gt", "value": 2}]))
    assert "filters" not in request
    assert notes == [NOTE_RATIO, NOTE_METRIC_FILTER]


def test_top_n_becomes_a_top_n_filter():
    request, notes = build_visual_request(plan(
        limit=5, order_by=[{"field": "Net Sales", "direction": "desc"}]))
    assert notes == []
    assert request["filters"] == [{
        "kind": "topN", "target": REGION, "operator": "Top", "itemCount": 5,
        "orderBy": NET}]
    request, _ = build_visual_request(plan(
        limit=3, order_by=[{"field": "Net Sales", "direction": "asc"}]))
    assert request["filters"][0]["operator"] == "Bottom"


def test_limit_that_cannot_be_expressed_is_reported():
    request, notes = build_visual_request(plan(
        group_by=[], limit=1,
        order_by=[{"field": "Net Sales", "direction": "desc"}]))
    assert request["visualType"] == "card"
    assert notes == [NOTE_LIMIT]


def test_title_is_the_question_trimmed():
    request, _ = build_visual_request(plan(), "  Net   sales\nby region  ")
    assert request["title"] == "Net sales by region"
    request, _ = build_visual_request(plan(), "x" * 300)
    assert len(request["title"]) == 120


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
    assert agent.pending_visual_request["visualType"] == "clusteredColumnChart"
    assert agent.pending_visual_request["title"] == "Net sales by region"
    assert "A chart of this answer replaced the one" in answer
    assert agent.last_trace["chart"] == "created"
    # The note sits with the table, before the collapsible details.
    assert answer.index("A chart of this answer") < answer.index(
        "**Query plan and DAX**")


def test_every_answered_question_gets_a_visual():
    filtered = plan(limit=3, order_by=[{"field": "Net Sales", "direction": "desc"}],
                    filters=[column_filter("DimDate", "Year", "eq", 2026)])
    for reply in (plan(), plan(group_by=[]), filtered,
                  plan(group_by=[("DimStore", "Region"), ("FactSales", "Channel")])):
        agent = agent_for(reply, options={"create_visuals": True})
        agent.ask("Net sales question")
        assert agent.pending_visual_request is not None, reply


def test_agent_reports_what_the_chart_leaves_out():
    agent = agent_for(plan(metrics=["Net Sales", RATIO]),
                      options={"create_visuals": True})
    answer = agent.ask("Net sales and units per order by region")
    assert agent.pending_visual_request is not None
    assert "Note: a calculated ratio is not shown in the chart" in answer


def test_agent_says_when_no_chart_is_possible():
    agent = agent_for(plan(metrics=[RATIO]), options={"create_visuals": True})
    answer = agent.ask("Units per order by region")
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


# ---- Pie charts and named chart types ----------------------------------

from src.visual_builder import chart_preference  # noqa: E402


@pytest.mark.parametrize("question,expected_type,cleaned", [
    ("Show net sales by region as a pie chart.", "pieChart",
     "Show net sales by region."),
    ("Pie chart of gross profit by channel", "pieChart",
     "Gross profit by channel"),
    ("Show me a donut chart showing units sold by store type", "donutChart",
     "Show me units sold by store type"),
    ("Net sales by month in a line graph", "lineChart", "Net sales by month"),
    ("Give me a bar chart for net sales by brand", "clusteredBarChart",
     "Give me net sales by brand"),
    ("Net sales by region", None, "Net sales by region"),
    ("Which bar sold the most?", None, "Which bar sold the most?"),
    # A chart without a type: the words go, the type is chosen later.
    ("Create chart with net profit over years.", None,
     "Net profit over years."),
    ("Create a pie chart of net sales by region", "pieChart",
     "Net sales by region"),
    ("Make a graph of units sold by category", None,
     "Units sold by category"),
    ("Plot net sales by month", None, "Net sales by month"),
    ("Net sales by year as a chart", None, "Net sales by year"),
    ("Show gross profit by sales channel.", None,
     "Show gross profit by sales channel."),
])
def test_chart_preference(question, expected_type, cleaned):
    assert chart_preference(question) == (expected_type, cleaned)


def test_small_part_of_whole_answer_becomes_a_pie():
    request, _ = build_visual_request(
        plan(group_by=[("FactSales", "Channel")]),
        "Show gross profit by sales channel.", row_count=3)
    assert request["visualType"] == "pieChart"
    assert [f["role"] for f in request["fields"]] == ["Category", "Y"]


@pytest.mark.parametrize("question,row_count,expected", [
    ("Net sales by region", 5, "pieChart"),
    ("Net sales by region", 9, "clusteredColumnChart"),       # too many slices
    ("Net sales by region", None, "clusteredColumnChart"),    # size unknown
    ("Compare net sales across regions", 5, "clusteredColumnChart"),
    ("Which region has the highest net sales?", 5, "clusteredColumnChart"),
    ("What is the share of net sales by region?", 9, "pieChart"),
    ("Net sales split by region", None, "pieChart"),
    ("Share of net sales by region", 20, "clusteredColumnChart"),
])
def test_when_a_pie_is_chosen(question, row_count, expected):
    request, _ = build_visual_request(plan(), question, row_count=row_count)
    assert request["visualType"] == expected


def test_no_automatic_pie_for_time_ratios_or_counts():
    request, _ = build_visual_request(
        plan(group_by=[("DimDate", "Quarter")]), "Net sales by quarter", row_count=4)
    assert request["visualType"] == "lineChart"
    request, _ = build_visual_request(
        plan(metrics=("Margin %",)), "Margin % by region", row_count=5)
    assert request["visualType"] == "clusteredColumnChart"
    request, _ = build_visual_request(
        plan(metrics=("Variance",)), "Variance by region", row_count=5)
    assert request["visualType"] == "clusteredColumnChart"
    request, _ = build_visual_request(
        plan(metrics=[CUSTOMERS]), "Customers by region", row_count=5)
    assert request["visualType"] == "clusteredColumnChart"


def test_a_named_chart_type_wins():
    request, _ = build_visual_request(
        plan(), "Net sales by region", preferred_type="donutChart", row_count=30)
    assert request["visualType"] == "donutChart"
    request, _ = build_visual_request(
        plan(group_by=[("DimDate", "MonthName")]), "Net sales by month",
        preferred_type="clusteredBarChart")
    assert request["visualType"] == "clusteredBarChart"


def test_agent_plans_without_the_chart_words_and_draws_the_named_chart():
    seen = {}

    def generate(model, question, instructions):
        seen["question"] = question
        return json.dumps(plan())

    agent = StructuredPlanningAgent(
        model="m", powerbi=FakePowerBI(), generate=generate,
        chat=lambda model, messages: json.dumps({"faithful": True, "reason": ""}),
        options={"create_visuals": True})
    answer = agent.ask("Show net sales by region as a pie chart.")
    assert seen["question"] == "Show net sales by region."
    assert agent.pending_visual_request["visualType"] == "pieChart"
    assert agent.pending_visual_request["title"] == "Show net sales by region."
    assert agent.last_trace["chart_requested"] == "pieChart"
    assert "A chart of this answer replaced" in answer


def test_agent_leaves_the_question_alone_when_charts_are_off():
    seen = {}

    def generate(model, question, instructions):
        seen["question"] = question
        return json.dumps(plan())

    agent = StructuredPlanningAgent(
        model="m", powerbi=FakePowerBI(), generate=generate,
        chat=lambda model, messages: json.dumps({"faithful": True, "reason": ""}))
    agent.ask("Net sales by region as a pie chart")
    assert seen["question"] == "Net sales by region as a pie chart"
