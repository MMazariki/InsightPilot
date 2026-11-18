"""The data a report visual plots, read from Power BI, for the email chart.

The visual in the report is built from the answer's plan, but it does not
always plot the answer's table: "Which store grew the most between 2021 and
2026?" answers with a ranking (2021, 2026, change), while the report draws
net sales for every year 2021-2026 with one line per store. To make the
email show the same chart, the visual request itself (its fields and
filters, ``src/visual_builder.py``) is turned into one read-only DAX query.

``fetch(request, execute)`` returns::

    {"type": "lineChart", "category": "Year", "series": "Store Name" | None,
     "measures": ["Net Sales"], "percent": [False],
     "points": [{"category": 2021, "series": "Riyadh Mall 1", "values": [12975.4]}]}

or ``None`` for visuals with no chart picture (card, table) or a request it
cannot read. Rules follow Power BI's defaults: a top-N filter keeps the top
(or bottom) N categories by its measure; a measure filter keeps the points
that pass it.
"""

import datetime as dt
import re

from evaluation.query_plan_compiler import column_literal, dax_literal, field_reference

CHART_TYPES = {"clusteredColumnChart", "clusteredBarChart", "lineChart", "pieChart",
               "donutChart", "lineClusteredColumnComboChart"}
MAX_POINTS = 2000
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
TIME_COLUMNS = {"Date", "Year", "Quarter", "MonthNumber", "MonthName", "YearMonth",
                "WeekNumber", "DayName"}
_COMPARE = {"GreaterThan": ">", "GreaterThanOrEqual": ">=", "LessThan": "<",
            "LessThanOrEqual": "<=", "Is": "=", "IsNot": "<>"}
_PYTHON_COMPARE = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
                   "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
                   "=": lambda a, b: a == b, "<>": lambda a, b: a != b}


def _words(name):
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)


def _expression(target):
    if target.get("measure"):
        return "[" + target["measure"].replace("]", "]]") + "]"
    if target.get("column") and target.get("aggregationFunction") == "Count":
        return f"DISTINCTCOUNT({field_reference(target['table'], target['column'])})"
    raise ValueError("unsupported field")


def _name(target):
    if target.get("measure"):
        return target["measure"]
    return f"Count of {_words(target['column'])}"


def _column_filter(item):
    target = item["target"]
    column = field_reference(target["table"], target["column"])
    all_values = f"ALL({column})"
    if item["kind"] == "basic":
        values = ", ".join(column_literal(target["table"], target["column"], v)
                           for v in item["values"])
        test = f"{column} IN {{{values}}}"
        if item.get("operator") == "NotIn":
            test = f"NOT ({test})"
        return f"FILTER({all_values}, {test})"
    operator = _COMPARE[item["operator"]]
    value = item["value"]
    if isinstance(value, str) and value.endswith("T00:00:00"):
        value = value[:10]
    return (f"FILTER({all_values}, {column} {operator} "
            f"{column_literal(target['table'], target['column'], value)})")


def build_query(request):
    """``(dax, plan)`` for a visual request, or ``None`` if it has no chart
    picture. ``plan`` says which result column is what."""

    if request.get("visualType") not in CHART_TYPES:
        return None
    fields = request.get("fields") or []
    role = {f["role"]: f["target"] for f in fields}
    category = role.get("Category")
    if not category or "column" not in category or category.get("aggregationFunction"):
        return None
    measures = [role[r] for r in ("Y", "Y2") if r in role]
    if not measures:
        return None
    series = role.get("Series")
    filters, measure_filters, top = [], [], None
    for item in request.get("filters") or []:
        target = item.get("target") or {}
        if item.get("kind") == "topN":
            top = item
        elif target.get("measure") or target.get("aggregationFunction"):
            measure_filters.append(item)
        elif target.get("column"):
            filters.append(_column_filter(item))

    groups = [field_reference(category["table"], category["column"])]
    if series:
        groups.append(field_reference(series["table"], series["column"]))
    extra = [m for m in [f["target"] for f in measure_filters]
             + ([top["orderBy"]] if top else []) if m not in measures]
    columns = []
    for index, target in enumerate(measures + extra):
        columns.append(f'"v{index}", {_expression(target)}')
    body = ",\n    ".join(groups + filters + columns)
    dax = f"EVALUATE\nSUMMARIZECOLUMNS(\n    {body}\n)"
    plan = {"category": category, "series": series, "measures": measures,
            "extra": extra, "measure_filters": measure_filters, "top": top}
    return dax, plan


def _value(row, key):
    for name, value in row.items():
        if name.strip("[]") == key or name.endswith(f"[{key}]"):
            return value
    return None


def _sort_key(column, value):
    """Time categories in time order (months and days by the calendar)."""

    if column == "MonthName" and value in MONTHS:
        return (0, MONTHS.index(value))
    if column == "DayName" and value in DAYS:
        return (0, DAYS.index(value))
    if isinstance(value, (int, float)):
        return (0, value)
    return (1, str(value))


def category_text(column, value):
    """How the category reads on the axis: 2021, Jan 2026, 2026-11-16."""

    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str) and re.match(r"\d{4}-\d{2}-\d{2}T", value):
        day = dt.date.fromisoformat(value[:10])
        return day.strftime("%b %Y") if column == "YearMonth" else day.isoformat()
    return str(value)


def fetch(request, execute):
    """The points of the visual (see the module docstring), or ``None``.
    ``execute(dax)`` runs a read-only query and returns ``{"rows": [...]}``."""

    built = build_query(request)
    if built is None:
        return None
    dax, plan = built
    result = execute(dax)
    rows = result.get("rows") or []
    if len(rows) > MAX_POINTS:
        return None
    category, series = plan["category"], plan["series"]
    targets = plan["measures"] + plan["extra"]
    points = []
    for row in rows:
        values = [_value(row, f"v{i}") for i in range(len(targets))]
        point = {"category": _value(row, category["column"]),
                 "series": _value(row, series["column"]) if series else None,
                 "values": values[:len(plan["measures"])], "all": values}
        if point["category"] is None:
            continue
        points.append(point)

    # Measure filters (visual level): keep the points that pass.
    for item in plan["measure_filters"]:
        index = targets.index(item["target"])
        compare = _PYTHON_COMPARE[_COMPARE[item["operator"]]]
        points = [p for p in points if p["all"][index] is not None
                  and compare(p["all"][index], item["value"])]
    # Top / bottom N categories by the order measure (over all series).
    if plan["top"]:
        index = targets.index(plan["top"]["orderBy"])
        totals = {}
        for p in points:
            totals[p["category"]] = totals.get(p["category"], 0) + (p["all"][index] or 0)
        keep = sorted(totals, key=totals.get, reverse=plan["top"]["operator"] == "Top")
        keep = set(keep[:int(plan["top"]["itemCount"])])
        points = [p for p in points if p["category"] in keep]

    is_time = category["column"] in TIME_COLUMNS
    if is_time:
        points.sort(key=lambda p: (_sort_key(category["column"], p["category"]),
                                   str(p["series"])))
    for p in points:
        p.pop("all", None)
        p["label"] = category_text(category["column"], p["category"])
    names = [_name(t) for t in plan["measures"]]
    return {"type": request["visualType"], "category": _words(category["column"]),
            "category_column": category["column"], "time": is_time,
            "series": _words(series["column"]) if series else None,
            "measures": names, "percent": [n.rstrip().endswith("%") for n in names],
            "points": points}
