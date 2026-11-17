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
                    | "pieChart" | "donutChart"
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
* one breakdown       pie for a part-of-whole answer with up to six slices
                      (or when the question says share, split, breakdown
                      ...); otherwise column, bar for long lists, line for
                      time; column-and-line combination for two metrics.
                      A chart type named in the question (pie, donut, bar,
                      column, line) wins.
* two breakdowns      column or line chart with the second as the legend
* three or more       table

Metrics: a model measure is bound as a measure; a distinct count is bound as
the column with a distinct-count aggregation; a ratio of two expressions
cannot be bound to a visual and is left out (``notes`` says so). If nothing
can be bound, no visual is built.
"""

import re

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

# Chart types a question can ask for by name ("... as a pie chart").
CHART_NAMES = {
    "pie": "pieChart",
    "donut": "donutChart",
    "doughnut": "donutChart",
    "bar": "clusteredBarChart",
    "column": "clusteredColumnChart",
    "line": "lineChart",
}
_CHART_NOUN = r"(?:chart|graph|diagram)"
_CHART_TYPE = "(" + "|".join(CHART_NAMES) + ")"
_CHART_PATTERNS = [
    # "... as a pie chart", "... in a donut chart"
    re.compile(rf"\s*\b(?:as|in|on|using|with)\s+(?:an?\s+)?{_CHART_TYPE}\s+{_CHART_NOUN}\b", re.I),
    # "pie chart of ...", "a bar chart showing ..."
    re.compile(rf"\b(?:an?\s+)?{_CHART_TYPE}\s+{_CHART_NOUN}\s+(?:of|for|showing|with)\s+", re.I),
    # any other mention
    re.compile(rf"\b(?:an?\s+)?{_CHART_TYPE}\s+{_CHART_NOUN}\b", re.I),
]

# Words that ask for parts of a whole.
_SHARE_WORDS = re.compile(
    r"\b(share|shares|split|breakdown|proportion|proportions|mix|composition|"
    r"distribution|contribution|percentage of|percent of|portion)\b", re.I)
# Words that ask for a comparison or ranking, where bars read better.
_COMPARE_WORDS = re.compile(
    r"\b(compare|comparison|versus|vs|rank|ranking|top|bottom|most|least|"
    r"highest|lowest|best|worst|trend|over time)\b", re.I)
# Measures that do not add up to a meaningful whole.
NON_ADDITIVE_MEASURES = {"Variance", "Sales Variance"}
PIE_MAX_SLICES = 6

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


# A chart asked for without a type ("create a chart of ...", "... as a
# graph"): the words are removed, the type is chosen from the answer.
_ANY_CHART = r"(?:chart|graph|plot|visual|visuali[sz]ation|diagram)"
_GENERIC_PATTERNS = [
    re.compile(rf"\b(?:an?\s+)?{_ANY_CHART}\s+(?:of|for|showing|with|on|about)\s+", re.I),
    re.compile(rf"\s*\b(?:as|in|on|using|with)\s+(?:an?\s+)?{_ANY_CHART}\b", re.I),
    re.compile(rf"\b(?:an?\s+)?{_ANY_CHART}\b", re.I),
]
# Request verbs left at the start once the chart words are gone
# ("Create net profit by year" -> "Net profit by year").
_LEAD_VERB = re.compile(
    r"^\s*(?:please\s+)?(?:create|make|draw|build|plot|generate|produce|"
    r"visuali[sz]e|chart|graph)\s+(?:me\s+)?", re.I)
# Verbs that mean "draw it" even when no chart noun is present.
_DRAW_VERB = re.compile(
    r"^\s*(?:please\s+)?(?:draw|plot|visuali[sz]e|chart|graph)\s+(?:me\s+)?", re.I)


def _tidy(text):
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([.?!,])", r"\1", text)
    text = re.sub(r"^[.?!,]+\s*", "", text)
    return text[0].upper() + text[1:] if text else text


def chart_preference(question):
    """Return ``(visual_type, question_without_the_chart_words)``.

    "Show net sales by region as a pie chart." -> ("pieChart", "Show net
    sales by region."). "Create chart with net sales over years." -> (None,
    "Net sales over years."). The cleaned question is what the planner and
    the answer checks see: "create", "pie" and "chart" are not words of the
    data model. ``visual_type`` is ``None`` when no chart type was named.
    """

    text = str(question or "")
    visual_type = None
    match = None
    for pattern in _CHART_PATTERNS:
        match = pattern.search(text)
        if match:
            visual_type = CHART_NAMES[match.group(1).lower()]
            break
    if match is None:
        for pattern in _GENERIC_PATTERNS:
            match = pattern.search(text)
            if match:
                break

    if match is not None:
        cleaned = text[:match.start()] + " " + text[match.end():]
        cleaned = _LEAD_VERB.sub("", _tidy(cleaned))
    else:
        cleaned = _DRAW_VERB.sub("", text)
    cleaned = _tidy(cleaned)
    if not cleaned or cleaned == _tidy(text):
        return visual_type, text
    return visual_type, cleaned


# Chart types offered by the selector above the report, by label.
CHART_CHOICES = {
    "Pie": "pieChart",
    "Donut": "donutChart",
    "Column": "clusteredColumnChart",
    "Bar": "clusteredBarChart",
    "Line": "lineChart",
}


def chart_choices(request):
    """Labels of the chart types that can show ``request``'s fields: pie
    and donut need one breakdown and one measure; a second breakdown
    (legend) allows column, bar and line. Cards, tables and two-measure
    combinations offer none."""

    if not request:
        return []
    roles = [field["role"] for field in request.get("fields") or []]
    if roles.count("Category") != 1 or roles.count("Y") != 1 \
            or set(roles) - {"Category", "Y", "Series"}:
        return []
    if "Series" in roles:
        return ["Column", "Bar", "Line"]
    return list(CHART_CHOICES)


def with_chart_type(request, label):
    """A copy of ``request`` drawn as the chosen type (``None`` = as built)."""

    new = dict(request)
    if label in CHART_CHOICES:
        new["visualType"] = CHART_CHOICES[label]
    return new


def _is_additive(target):
    name = target.get("measure")
    return bool(name) and "%" not in name and name not in NON_ADDITIVE_MEASURES


def _wants_pie(question, row_count, target, category):
    """Part-of-whole answers with few slices read best as a pie."""

    if category["column"] in TIME_COLUMNS or not _is_additive(target):
        return False
    text = str(question or "")
    if _SHARE_WORDS.search(text):
        return row_count is None or row_count <= PIE_MAX_SLICES * 2
    if _COMPARE_WORDS.search(text):
        return False
    return row_count is not None and 2 <= row_count <= PIE_MAX_SLICES


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
            value = item["value"]
            # Report filters on date columns take a date-time value.
            if item.get("column") in ("Date", "YearMonth") and isinstance(value, str) \
                    and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                value += "T00:00:00"
            filters.append({
                "kind": "advanced", "target": target,
                "operator": ADVANCED_OPERATORS[operator], "value": value,
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


def build_visual_request(plan, question=None, model=None, preferred_type=None,
                         row_count=None):
    """Return ``(request, notes)``.

    ``request`` is the dictionary for the report component, or ``None`` when
    nothing in the answer can be shown (``notes`` then holds the reason).
    ``notes`` lists, in plain words, anything in the answer that the visual
    does not reflect. ``preferred_type`` is a chart type the question asked
    for by name; ``row_count`` is how many rows the answer has (used to
    decide whether a pie is readable).
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
            if preferred_type is not None:
                visual_type = preferred_type
            elif _wants_pie(question, row_count, metric_targets[0], category):
                visual_type = "pieChart"
            elif is_time:
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
