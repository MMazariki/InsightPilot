"""Growth, share and holiday-period questions on top of a checked plan.

A query plan computes measures broken down and filtered by columns. It
cannot say "the change from 2021 to 2026", "delivery's share of net
sales" or "during Ramadan 2024". This module recognises those requests in
the question, takes them out before planning, adjusts the checked plan
with fixed rules and computes the final figures from the query result:

* growth   "Which store grew the most between 2021 and 2026?"
           -> plan by store and year, then per store: value in each year,
              change and change %, ranked by change %.
           "Net sales growth year over year" -> change against the year
              before, for every year.
* share    "Delivery share of net sales by year"
           -> plan by year and channel, then delivery's % of each year.
           "Share of net sales by channel" -> each channel's % of the total.
* periods  "Orders in Ramadan 2024", "Sales during Eid 2023",
           "White Friday 2025", "National Day 2024"
           -> a date-range filter on DimDate[Date].

Nothing here calls a model.
"""

import datetime as dt
import re

from evaluation.semantic_checks import load_catalog

# --------------------------------------------------------------- periods

RAMADAN = {
    2021: (dt.date(2021, 4, 13), dt.date(2021, 5, 12)),
    2022: (dt.date(2022, 4, 2), dt.date(2022, 5, 1)),
    2023: (dt.date(2023, 3, 23), dt.date(2023, 4, 20)),
    2024: (dt.date(2024, 3, 11), dt.date(2024, 4, 9)),
    2025: (dt.date(2025, 3, 1), dt.date(2025, 3, 29)),
    2026: (dt.date(2026, 2, 18), dt.date(2026, 3, 19)),
}


def _white_friday(year):
    last = dt.date(year, 11, 30)
    friday = last - dt.timedelta(days=(last.weekday() - 4) % 7)
    return friday, friday + dt.timedelta(days=2)


def period_range(name, year):
    """``(first day, last day)`` of a named period in a year, or ``None``."""

    name = name.lower()
    if name == "ramadan":
        return RAMADAN.get(year)
    if name.startswith("eid"):
        ramadan = RAMADAN.get(year)
        if not ramadan:
            return None
        start = ramadan[1] + dt.timedelta(days=1)
        return start, start + dt.timedelta(days=3)
    if name == "national day":
        day = dt.date(year, 9, 23)
        return day, day
    if name == "white friday":
        return _white_friday(year)
    return None


_PERIOD = re.compile(
    r"\b(?:in|during|over|for|on|around)?\s*(?:the\s+)?"
    r"(ramadan|eid(?: al[- ]fitr)?|national day|white friday)"
    r"(?:\s+(?:of\s+)?(\d{4}))?", re.I)

# ---------------------------------------------------------------- growth

# Words that ask for growth on their own, and words that do only when two
# years are named ("change the target" is a request, not a question).
_GROWTH_WORDS = re.compile(
    r"\b(grew|grow|grows|grown|growth|declined|declining|decreased|dropped|"
    r"improved)\b", re.I)
_CHANGE_WORDS = re.compile(
    r"\b(change[sd]?|increase[sd]?|increasing|decline|decrease|drop|improve)\b",
    re.I)
_NEGATIVE = re.compile(r"\b(declin\w*|decreas\w*|drop\w*|fell|fall\w*)\b", re.I)
_YOY = re.compile(r"\b(year[- ]over[- ]year|yoy|year[- ]on[- ]year|annual growth|"
                  r"growth (?:by|per|each) year|each year'?s growth)\b", re.I)
_BETWEEN = re.compile(
    r"\b(?:between|from)\s+(\d{4})\s+(?:and|to|until|till|-)\s+(\d{4})\b"
    r"|\b(\d{4})\s+(?:vs\.?|versus|compared (?:to|with)|against|to)\s+(\d{4})\b",
    re.I)
_MOST = re.compile(r"\b(most|fastest|highest|biggest|largest|best|top)\b", re.I)
_LEAST = re.compile(r"\b(least|slowest|lowest|smallest|worst|bottom)\b", re.I)

# ----------------------------------------------------------------- share

_SHARE = re.compile(
    r"\b(share|percentage|percent|proportion|portion|contribution|mix)\b"
    r"(?:\s+of)?", re.I)
# Columns whose values can be named in a share question.
SHARE_COLUMNS = [
    ("FactSales", "Channel"), ("FactSales", "PaymentMethod"),
    ("DimProduct", "Category"), ("DimProduct", "Subcategory"),
    ("DimProduct", "Brand"), ("DimStore", "Region"), ("DimStore", "StoreType"),
    ("DimStore", "City"), ("DimCustomer", "CustomerSegment"),
    ("DimCustomer", "Gender"),
]
TIME_COLUMNS = {"Date", "Year", "Quarter", "MonthNumber", "MonthName",
                "YearMonth", "WeekNumber", "DayName"}
_DATE_COLUMNS = TIME_COLUMNS


def _named_value(question):
    """The first catalog value named in the question, as (table, column, value)."""

    catalog = load_catalog()
    for table, column in SHARE_COLUMNS:
        for value in catalog.get(f"{table}[{column}]", []):
            if re.search(rf"(?<![\w&]){re.escape(value)}(?![\w&])", question, re.I):
                return table, column, value
    return None


# Breakdown words, in the order they are looked for, and the planner wording.
_BREAKDOWNS = [
    (r"store types?", "store type"), (r"stores?|branch(?:es)?|shops?", "store"),
    (r"regions?", "region"), (r"cit(?:y|ies)", "city"),
    (r"subcategor(?:y|ies)", "subcategory"), (r"categor(?:y|ies)", "category"),
    (r"brands?", "brand"), (r"products?", "product"),
    (r"(?:sales )?channels?", "channel"), (r"payment methods?", "payment method"),
    (r"customer segments?|segments?", "customer segment"),
    (r"genders?", "gender"), (r"age bands?", "age band"),
    (r"quarters?", "quarter"), (r"months?", "month"), (r"years?", "year"),
]


def _rebuild(text, spec):
    """For growth and share, the rest of the question can describe the
    answer's layout ("ranking with change % for each store"), which is not
    for the planner. Keep only the measure, the breakdowns and any named
    values: "<measure> by <breakdowns> for <values>"."""

    from evaluation.query_plan_validator import load_model

    lower = text.lower()
    measures = sorted((m["Name"] for t in load_model()["Tables"]
                       for m in t.get("Measures", [])), key=len, reverse=True)
    metric = next((m for m in measures
                   if re.search(rf"\b{re.escape(m.lower())}", lower)), None)
    if metric is None:
        for words, name in [(r"orders?", "number of distinct orders"),
                            (r"units?", "units sold"), (r"profit", "gross profit"),
                            (r"discounts?", "total discounts"),
                            (r"margin", "gross margin %")]:
            if re.search(rf"\b{words}\b", lower):
                metric = name
                break
    if metric is None and "number of distinct orders" in lower:
        metric = "number of distinct orders"
    metric = (metric or "net sales").lower()

    groups = []
    for words, name in _BREAKDOWNS:
        if re.search(rf"\b(?:{words})\b", lower) and name not in groups \
                and not (name == "year" and spec.get("growth")):
            groups.append(name)
            lower = re.sub(rf"\b(?:{words})\b", " ", lower)
    values = []
    catalog = load_catalog()
    for table, column in SHARE_COLUMNS:
        for value in catalog.get(f"{table}[{column}]", []):
            if re.search(rf"(?<![\w&]){re.escape(value)}(?![\w&])", text, re.I) \
                    and value not in values:
                values.append(value)
    question = metric[0].upper() + metric[1:]
    if groups:
        question += " by " + " and ".join(groups)
    if values:
        question += " for " + " and ".join(values)
    return question


def _tidy(text):
    text = re.sub(r"\s+", " ", text).strip(" ,;:")
    text = re.sub(r"\s+([.?!,])", r"\1", text)
    return text[0].upper() + text[1:] if text else text


def detect(question, today=None):
    """Return ``(question_for_the_planner, spec, notes)``.

    ``spec`` is ``None`` when the question needs none of this; otherwise a
    dict with any of the keys ``period``, ``growth`` and ``share``.
    """

    today = today or dt.date.today()
    text = str(question or "")
    spec, notes = {}, []

    # Holiday periods
    match = _PERIOD.search(text)
    if match:
        name = match.group(1).lower()
        name = "eid" if name.startswith("eid") else name
        year = int(match.group(2)) if match.group(2) else None
        if year is None:
            years = [y for y in sorted(RAMADAN, reverse=True)
                     if period_range(name, y) and period_range(name, y)[1] <= today]
            year = years[0] if years else None
        span = period_range(name, year) if year else None
        if span:
            spec["period"] = {"name": name, "year": year,
                              "start": span[0].isoformat(),
                              "end": span[1].isoformat()}
            label = name.title() if name != "eid" else "Eid al-Fitr"
            notes.append(f'"{match.group(0).strip()}" as {label} {year} '
                         f"({span[0]:%d %b} to {span[1]:%d %b %Y})")
            text = text[:match.start()] + " " + text[match.end():]
        else:
            notes.append(f"no dates are known for {match.group(1)} {year or ''}".strip())

    # Growth
    between = _BETWEEN.search(text)
    yoy = _YOY.search(text)
    if _GROWTH_WORDS.search(text) or yoy or (between and (
            re.search(r"\bcompar", text, re.I) or _CHANGE_WORDS.search(text))):
        # "declined the most" ranks the lowest change first.
        least = bool(_LEAST.search(text))
        if _NEGATIVE.search(text):
            least = not least
        growth = {"mode": "yoy" if yoy else "range", "years": None,
                  "direction": "asc" if least else "desc"}
        if between:
            years = [int(y) for y in between.groups() if y]
            growth["years"] = sorted(years[:2])
            text = text[:between.start()] + " " + text[between.end():]
        elif not yoy:
            growth["years"] = None  # first and last year in the data
        text = _GROWTH_WORDS.sub(" ", text)
        text = _CHANGE_WORDS.sub(" ", text)
        text = re.sub(r"\bhow (did|has|have|does|do)\b", " ", text, flags=re.I)
        text = _YOY.sub(" ", text)
        text = re.sub(r"\b(the )?(most|least|fastest|slowest)\b", " ", text, flags=re.I)
        text = re.sub(r"\bcompar(e|ed|ing|ison)\b", " ", text, flags=re.I)
        spec["growth"] = growth
        if growth["years"]:
            first, last = growth["years"]
            notes.append(f"growth from {first} to {last}"
                         + (" (year to date)" if last == today.year else ""))
        elif yoy:
            notes.append("growth against the year before")

    # Share
    if "growth" not in spec and _SHARE.search(text):
        named = _named_value(text)
        share = {"value": None, "table": None, "column": None}
        if named:
            table, column, value = named
            share.update(table=table, column=column, value=value)
            text = re.sub(rf"(?<![\w&]){re.escape(value)}(?![\w&])('s)?", " ",
                          text, count=1, flags=re.I)
            notes.append(f"{value}'s share of each total")
        text = _SHARE.sub(" ", text)
        spec["share"] = share

    if not spec:
        return question, None, []

    text = re.sub(r"\b(of|in|for|during|between|and|by)\s*([?.!]|$)", r"\2", text.strip())
    text = re.sub(r"^\s*(of|in|for|the)\b", "", text, flags=re.I)
    # "Which store ..." asks for a ranking of stores: plan "by store".
    text = re.sub(r"\bwhich\s+(\w+)", r"by \1", text, flags=re.I)
    from evaluation.answerability import names_a_metric

    cleaned = _tidy(text)
    named = names_a_metric(cleaned, strict=True)
    if spec.get("growth") or spec.get("share"):
        cleaned = _rebuild(cleaned, spec)
    elif not named:
        cleaned = _tidy("net sales " + cleaned[:1].lower() + cleaned[1:])
    if not named:
        notes.append("no measure named, so net sales")
    return cleaned, spec, notes


# ------------------------------------------------------------------ plan

YEAR = {"table": "DimDate", "column": "Year"}


def _without_time_filters(filters, columns):
    return [f for f in filters if not (
        f.get("type") == "column" and f.get("table") == "DimDate"
        and f.get("column") in columns)]


def adjust_plan(plan, spec):
    """The plan to run for ``spec``. Returns ``(plan, notes)``."""

    if not spec:
        return plan, []
    plan = {**plan, "filters": list(plan.get("filters") or []),
            "group_by": list(plan.get("group_by") or []),
            "order_by": list(plan.get("order_by") or [])}
    notes = []

    period = spec.get("period")
    if period:
        plan["filters"] = _without_time_filters(
            plan["filters"], {"Date", "YearMonth", "MonthName", "MonthNumber",
                              "Quarter", "Year"})
        plan["filters"] += [
            {"type": "column", "table": "DimDate", "column": "Date",
             "operator": "gte", "value": period["start"]},
            {"type": "column", "table": "DimDate", "column": "Date",
             "operator": "lte", "value": period["end"]},
        ]

    growth = spec.get("growth")
    if growth:
        plan["group_by"] = [g for g in plan["group_by"]
                            if not (g["table"] == "DimDate" and g["column"] != "Year")]
        if YEAR not in plan["group_by"]:
            plan["group_by"].append(dict(YEAR))
        plan["filters"] = _without_time_filters(plan["filters"], {"Year"})
        if growth.get("years"):
            first, last = growth["years"]
            plan["filters"] += [
                {"type": "column", "table": "DimDate", "column": "Year",
                 "operator": "gte", "value": first},
                {"type": "column", "table": "DimDate", "column": "Year",
                 "operator": "lte", "value": last},
            ]
        # Ranking and limits apply to the change, after the query.
        spec["growth"]["limit"] = plan.get("limit")
        plan["limit"] = None
        plan["order_by"] = []
        plan["metrics"] = plan["metrics"][:1]

    share = spec.get("share")
    if share:
        if share.get("column"):
            target = {"table": share["table"], "column": share["column"]}
            plan["filters"] = [f for f in plan["filters"] if not (
                f.get("type") == "column" and f.get("table") == target["table"]
                and f.get("column") == target["column"])]
            if target not in plan["group_by"]:
                plan["group_by"].append(target)
        share["limit"] = plan.get("limit")
        plan["limit"] = None
        plan["order_by"] = []
        plan["metrics"] = plan["metrics"][:1]
    return plan, notes


# ---------------------------------------------------------------- result


def _ref(group):
    return f"{group['table']}[{group['column']}]"


def _metric_column(columns, plan):
    alias = plan["metrics"][0]["alias"]
    for column in columns:
        if column.strip("[]") == alias or column.endswith(f"[{alias}]"):
            return column
    return columns[-1]


def _percent(value):
    return "" if value is None else f"{value * 100:,.1f}%"


def _share_column(groups, share):
    """The column a share is taken across (the rest are the totals' keys)."""

    if share.get("column"):
        return f"{share['table']}[{share['column']}]"
    # Share across the non-time grouping, within each time period.
    non_time = [c for c in groups if c.split("[")[-1].rstrip("]") not in TIME_COLUMNS]
    return non_time[-1] if non_time else (groups[-1] if groups else None)


# Measures whose total is not the sum of its parts.
NON_ADDITIVE = {"Orders", "Variance", "Sales Variance"}


def needs_counted_totals(plan):
    """True when a share's total must be counted, not added up: distinct
    counts (an order with lines in two channels is one order, APP2-023),
    ratios and percentages."""

    expression = (plan.get("metrics") or [{}])[0].get("expression") or {}
    if expression.get("kind") in ("distinct_count", "divide"):
        return True
    name = str(expression.get("name", ""))
    return name.endswith("%") or name in NON_ADDITIVE


def totals_plan(plan, spec):
    """The plan without the column the share is taken across: one total per
    outer group (for example per year), counted by Power BI."""

    groups = [_ref(g) for g in plan.get("group_by") or []]
    share_col = _share_column(groups, spec["share"])
    return dict(plan, group_by=[g for g in plan["group_by"] if _ref(g) != share_col],
                order_by=[], limit=None)


def totals_from(result, plan):
    """``{outer key: total}`` from the result of ``totals_plan``."""

    columns = result.get("columns") or []
    metric = _metric_column(columns, plan)
    keys = [c for c in columns if c != metric and not c.startswith("[")]
    return {tuple(r.get(c) for c in keys): r.get(metric) for r in result.get("rows") or []}


def finish(result, plan, spec, totals=None):
    """Turn the query result into the answer's table. Returns
    ``(result, notes)`` with ``columns`` and ``rows`` replaced. ``totals``:
    counted totals per outer group for a share (``totals_plan``); without
    them the parts are added up."""

    if not spec or not result.get("rows"):
        return result, []
    columns, rows = result["columns"], result["rows"]
    metric = _metric_column(columns, plan)
    notes = []

    growth = spec.get("growth")
    if growth:
        year_col = next((c for c in columns if c.endswith("[Year]")), None)
        if year_col is None:
            return result, []
        keys = [c for c in columns if c not in (metric, year_col)
                and not c.startswith("[")]
        years = sorted({r[year_col] for r in rows if r.get(year_col) is not None})
        table = {}
        for r in rows:
            table.setdefault(tuple(r.get(k) for k in keys), {})[r[year_col]] = r.get(metric)

        label = metric.strip("[]")
        out_rows = []
        if growth["mode"] == "yoy":
            out_cols = keys + ["Year", label, "Change", "Change %"]
            for key, values in table.items():
                previous = None
                for year in years:
                    value = values.get(year)
                    row = dict(zip(keys, key))
                    row.update({"Year": year, label: value})
                    if previous not in (None, 0) and value is not None:
                        row["Change"] = value - previous
                        row["Change %"] = _percent((value - previous) / previous)
                    else:
                        row["Change"], row["Change %"] = None, ""
                    out_rows.append(row)
                    previous = value
        else:
            first, last = (growth["years"] or [years[0], years[-1]])
            if not growth["years"]:
                notes.append(f"growth from {first} to {last}, the first and "
                             "last years in the data")
            out_cols = keys + [f"{label} {first}", f"{label} {last}",
                               "Change", "Change %"]
            ranked = []
            for key, values in table.items():
                a, b = values.get(first), values.get(last)
                change = None if a is None or b is None else b - a
                pct = None if change is None or not a else change / a
                row = dict(zip(keys, key))
                row.update({f"{label} {first}": a, f"{label} {last}": b,
                            "Change": change, "Change %": pct})
                ranked.append(row)
            reverse = growth["direction"] == "desc"
            ranked.sort(key=lambda r: (r["Change %"] is None,
                                       -(r["Change %"] or 0) if reverse
                                       else (r["Change %"] or 0)))
            if growth.get("limit"):
                ranked = ranked[:growth["limit"]]
            for row in ranked:
                row["Change %"] = _percent(row["Change %"])
            out_rows = ranked
            if keys:
                notes.append("ranked by change %")
        return {**result, "columns": out_cols, "rows": out_rows,
                "row_count": len(out_rows)}, notes

    share = spec.get("share")
    if share:
        groups = [c for c in columns if c != metric and not c.startswith("[")]
        share_col = _share_column(groups, share)
        outer = [c for c in groups if c != share_col]
        if totals is None:
            # Additive measures: the total is the sum of the parts.
            totals = {}
            for r in rows:
                key = tuple(r.get(c) for c in outer)
                totals[key] = totals.get(key, 0) + (r.get(metric) or 0)
        out_rows = []
        for r in rows:
            total = totals.get(tuple(r.get(c) for c in outer))
            row = dict(r)
            row["Share %"] = (r.get(metric) or 0) / total if total else None
            out_rows.append(row)
        if share.get("value"):
            out_rows = [r for r in out_rows
                        if str(r.get(share_col)).lower() == share["value"].lower()]
        out_rows.sort(key=lambda r: tuple(str(r.get(c)) for c in outer)
                      + (-(r["Share %"] or 0),))
        for row in out_rows:
            row["Share %"] = _percent(row["Share %"])
        out_cols = groups + [metric, "Share %"]
        return {**result, "columns": out_cols, "rows": out_rows,
                "row_count": len(out_rows)}, notes

    return result, notes


def visual_type(spec):
    """A chart type that suits the analysis, or ``None`` for the default."""

    if spec and spec.get("share"):
        return "hundredPercentStackedColumnChart"
    return None
