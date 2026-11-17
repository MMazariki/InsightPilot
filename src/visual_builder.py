"""Turns a validated query plan into a visual for the embedded Power BI report.

The original Azure AI Foundry agent asked its model to call a
``create_powerbi_visual`` tool. The local structured agent does not need a
model for this: the validated plan already says what the answer is broken
down by, which metrics it shows and how it is filtered, so the visual is
derived from the plan with fixed rules. Every answered question gets one,
and the report component replaces the previous visual with it.

Request sent to the report component (``powerbi_embed_component``):

    {
      "visualType": "clusteredColumnChart" | "clusteredBarChart" | "lineChart"
                    | "lineClusteredColumnComboChart" | "card" | "multiRowCard"
                    | "tableEx",
      "fields":  [{"role": "Category" | "Series" | "Y" | "Y2" | "Values",
                   "target": {table, column} | {table, measure}
                             | {table, column, aggregationFunction}}],
      "filters": [basic | advanced | topN],   # so the chart matches the table
      "title":   "the question",
    }

Shape of the visual:

* no breakdown        one metric -> card, several -> multi-row card
* one breakdown       column chart; bar for long lists; line for time;
                      column-and-line combination for two metrics
* two breakdowns      column or line chart with the second as the legend
* three or more       table

Metrics: a model measure is bound as a measure; a distinct count is bound as
the column with a distinct-count aggregation; a ratio of two expressions
cannot be bound to a visual and is left out (``notes`` says so). If nothing
can be bound, no visual is built.
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

ADVANCED_OPERATORS = {
    "gt": "GreaterThan",
    "gte": "GreaterThanOrEqual",
    "lt": "LessThan",
    "lte": "LessThanOrEqual",
}

REASON_NO_METRIC = "none of the answer's metrics can be shown in a report visual"
NOTE_RATIO = "a calculated ratio is not shown in the chart"
NOTE_METRIC_FILTER = "a condition on a calculated value is not applied to the chart"
NOTE_LIMIT = "the row limit is not applied to the chart"


def measure_tables(model=None):
    """Map each measure name to the table that owns it."""

    model = load_model() if model is None else model
    return {
        measure["Name"]: table["Name"]
        for table in model["Tables"]
        for measure in table.get("Measures", [])
    }


def _metric_target(expression, tables):
    """The report field for one plan expression, or ``None`` if unbindable."""

    kind = expression.get("kind")
    if kind == "measure" and expression.get("name") in tables:
        return {"table": tables[expression["name"]], "measure": expression["name"]}
    if kind == "distinct_count":
        # Power BI's "Count" aggregation is the distinct count.
        return {"table": expression["table"], "column": expression["column"],
                "aggregationFunction": "Count"}
    return None


def _filters(plan, targets_by_alias, category, notes):
    filters = []
    for item in plan.get("filters") or []:
        operator = item["operator"]
        if item.get("type") == "metric":
            target = targets_by_alias.get(item["alias"])
            if target is None:
                notes.append(NOTE_METRIC_FILTER)
                continue
        else:
            target = {"table": item["table"], "column": item["column"]}

        if operator in ("eq", "ne") and item.get("type") != "metric":
            filters.append({
                "kind": "basic", "target": target,
                "operator": "In" if operator == "eq" else "NotIn",
                "values": [item["value"]],
            })
        elif operator in ADVANCED_OPERATORS:
            filters.append({
                "kind": "advanced", "target": target,
                "operator": ADVANCED_OPERATORS[operator], "value": item["value"],
            })
        else:  # eq / ne on a metric
            filters.append({
                "kind": "advanced", "target": target,
                "operator": "Is" if operator == "eq" else "IsNot",
                "value": item["value"],
            })

    limit = plan.get("limit")
    if limit is not None:
        order = (plan.get("order_by") or [None])[0]
        order_target = targets_by_alias.get(order["field"]) if order else None
        if category is not None and order_target is not None:
            filters.append({
                "kind": "topN", "target": category,
                "operator": "Top" if order["direction"] == "desc" else "Bottom",
                "itemCount": limit, "orderBy": order_target,
            })
        else:
            notes.append(NOTE_LIMIT)
    return filters


def build_visual_request(plan, question=None, model=None):
    """Return ``(request, notes)``.

    ``request`` is the dictionary for the report component, or ``None`` when
    nothing in the answer can be shown (``notes`` then holds the reason).
    ``notes`` lists, in plain words, anything in the answer that the visual
    does not reflect.
    """

    tables = measure_tables(model)
    notes = []
    metric_targets = []
    targets_by_alias = {}
    for metric in plan.get("metrics") or []:
        target = _metric_target(metric.get("expression") or {}, tables)
        if target is None:
            if NOTE_RATIO not in notes:
                notes.append(NOTE_RATIO)
            continue
        targets_by_alias[metric["alias"]] = target
        if target not in metric_targets:
            metric_targets.append(target)
    if not metric_targets:
        return None, [REASON_NO_METRIC]

    groups = [{"table": g["table"], "column": g["column"]}
              for g in plan.get("group_by") or []]

    if not groups:
        visual_type = "card" if len(metric_targets) == 1 else "multiRowCard"
        fields = [{"role": "Values", "target": t} for t in metric_targets]
        category = None
    elif len(groups) >= 3:
        visual_type = "tableEx"
        fields = [{"role": "Values", "target": t} for t in groups + metric_targets]
        category = groups[0]
    else:
        # Put a time column on the axis when there is one.
        if len(groups) == 2 and groups[1]["column"] in TIME_COLUMNS \
                and groups[0]["column"] not in TIME_COLUMNS:
            groups.reverse()
        category = groups[0]
        fields = [{"role": "Category", "target": category}]
        is_time = category["column"] in TIME_COLUMNS
        if len(groups) == 2:
            visual_type = "lineChart" if is_time else "clusteredColumnChart"
            fields.append({"role": "Series", "target": groups[1]})
            fields.append({"role": "Y", "target": metric_targets[0]})
        elif len(metric_targets) >= 2:
            visual_type = "lineClusteredColumnComboChart"
            fields.append({"role": "Y", "target": metric_targets[0]})
            fields.append({"role": "Y2", "target": metric_targets[1]})
        else:
            if is_time:
                visual_type = "lineChart"
            elif category["column"] in LONG_LIST_COLUMNS:
                visual_type = "clusteredBarChart"
            else:
                visual_type = "clusteredColumnChart"
            fields.append({"role": "Y", "target": metric_targets[0]})

    request = {"visualType": visual_type, "fields": fields}
    filters = _filters(plan, targets_by_alias, category, notes)
    if filters:
        request["filters"] = filters
    if question:
        request["title"] = " ".join(str(question).split())[:120]
    return request, notes
