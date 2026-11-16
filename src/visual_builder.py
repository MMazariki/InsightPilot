"""Turns a validated query plan into a chart for the embedded Power BI report.

The original Azure AI Foundry agent asked its model to call a
``create_powerbi_visual`` tool. The local structured agent does not need a
model for this: the validated plan already says which column the answer is
broken down by and which measures it shows, so the chart is derived from the
plan with fixed rules.

The request has the same shape the Foundry agent produced (see
``src/powerbi_agent.py``), which the report component already understands:

    one measure   {"visualType", "category": {table, column},
                   "value": {table, measure}}
    two measures  {"visualType": "lineClusteredColumnComboChart",
                   "fields": [Category, Y, Y2]}

A chart is only built when it would show the same thing as the answer:

* exactly one breakdown column (a chart needs one axis);
* at least one metric that is a model measure (a chart field must be a real
  measure; computed metrics such as distinct counts cannot be bound);
* no filters and no row limit, because the chart cannot apply them and would
  silently show different numbers from the table.
"""

from evaluation.query_plan_validator import load_model

# Columns that read as a time axis.
TIME_COLUMNS = {
    "Date", "Year", "Quarter", "MonthNumber", "MonthName", "YearMonth",
    "WeekNumber", "DayName",
}
# Columns with many values read better as horizontal bars.
LONG_LIST_COLUMNS = {
    "Brand", "Subcategory", "City", "ProductName", "StoreName", "SKU",
    "StoreCode",
}

REASON_NO_BREAKDOWN = "the answer has no single breakdown to chart"
REASON_NO_MEASURE = "the answer's metric is computed, not a model measure"
REASON_FILTERED = (
    "the answer is filtered or limited, and the report chart would show "
    "unfiltered numbers"
)


def measure_tables(model=None):
    """Map each measure name to the table that owns it."""

    model = load_model() if model is None else model
    return {
        measure["Name"]: table["Name"]
        for table in model["Tables"]
        for measure in table.get("Measures", [])
    }


def build_visual_request(plan, model=None):
    """Return ``(request, reason)``.

    ``request`` is the dictionary for the report component, or ``None`` with
    ``reason`` saying in plain words why no chart fits this answer.
    """

    group_by = plan.get("group_by") or []
    if len(group_by) != 1:
        return None, REASON_NO_BREAKDOWN

    if plan.get("filters") or plan.get("limit") is not None:
        return None, REASON_FILTERED

    tables = measure_tables(model)
    measures = []
    for metric in plan.get("metrics") or []:
        expression = metric.get("expression") or {}
        name = expression.get("name")
        if expression.get("kind") == "measure" and name in tables:
            target = {"table": tables[name], "measure": name}
            if target not in measures:
                measures.append(target)
    if not measures:
        return None, REASON_NO_MEASURE

    category = {"table": group_by[0]["table"], "column": group_by[0]["column"]}

    if len(measures) >= 2:
        return {
            "visualType": "lineClusteredColumnComboChart",
            "fields": [
                {"role": "Category", "target": category},
                {"role": "Y", "target": measures[0]},
                {"role": "Y2", "target": measures[1]},
            ],
        }, None

    column = category["column"]
    if column in TIME_COLUMNS:
        visual_type = "lineChart"
    elif column in LONG_LIST_COLUMNS:
        visual_type = "clusteredBarChart"
    else:
        visual_type = "clusteredColumnChart"
    return {
        "visualType": visual_type,
        "category": category,
        "value": measures[0],
    }, None
