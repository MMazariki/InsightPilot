"""Step 1 of answering a data question: understand it.

The language model reads the user's message as plain English, with as much
flexibility as it can: typos, synonyms, slang, abbreviations, other
languages, relative dates ("the last two years", "since the spring"),
vague or incomplete wording. It does not refuse and does not ask the user
to rephrase; where the meaning is unclear it takes the most likely reading
and says what it assumed.

What it writes back is a *business request*: one explicit sentence that
names the model's measures, breakdowns, exact filter values and explicit
dates, plus any growth / share / holiday-period calculation as structured
fields. Step 2 (the planner) turns that request into the strict query plan,
and step 3 (validation, semantic checks, read-only DAX) applies the model's
rules. No fixed wording rules are applied to the user's text here.
"""

import calendar
import datetime as dt
import json
import re

from evaluation.query_plan_validator import load_model

CHARTS = {"column": "clusteredColumnChart", "bar": "clusteredBarChart",
          "line": "lineChart", "pie": "pieChart", "donut": "donutChart"}
HOLIDAYS = ("ramadan", "eid", "national day", "white friday")


def _vocabulary():
    from evaluation.semantic_checks import describe_catalog

    lines = []
    for table in load_model()["Tables"]:
        measures = [m["Name"] for m in table.get("Measures", [])]
        columns = [c["Name"] for c in table.get("Columns", [])]
        if measures:
            lines.append(f"Measures in {table['Name']}: {', '.join(measures)}")
        lines.append(f"Columns of {table['Name']}: {', '.join(columns)}")
    return "\n".join(lines) + "\n\nVALUES OF TEXT COLUMNS:\n" + describe_catalog()


def instructions(today=None):
    today = today or dt.date.today()
    return f"""You are the first step of a business-intelligence assistant for a retail \
sales Power BI model. Read the user's message as plain English and work out \
what they want to know, as generously and flexibly as a smart analyst would.

- Understand typos, synonyms, slang, abbreviations, other languages, relative \
dates and vague or incomplete wording.
- Never refuse and never ask the user to rephrase. When something is unclear, \
choose the most likely meaning and record it in "assumptions".
- A word that is not in the model means the closest measure, column or value \
(for example revenue or sales means Net Sales, branch means store). Record the \
mapping in "assumptions".
- When no measure is implied at all, use Net Sales and say so.

TODAY: {today.isoformat()} ({calendar.day_name[today.weekday()]}). Resolve every \
relative period against today into explicit years or dates. A span of the last \
N periods includes the current one unless the user asks for complete periods.

THE MODEL:
{_vocabulary()}

Reply with JSON only, every field present:
{{"measures": [], "breakdowns": [], "filters": [], "years": [],
  "date_from": null, "date_to": null, "sort": null, "top": null,
  "growth": null, "share": null, "holiday": null, "chart": null,
  "assumptions": [], "out_of_scope": false}}

"measures": what to calculate, using measure names from the model (or a short \
description such as "Net Sales per distinct customer" when no single measure \
fits).
"breakdowns": every column the user wants the result broken down by (by, per, \
each, for every, which ..., including time such as Year or YearMonth), as \
Table[Column]. Keep a breakdown even when the same column is also limited to \
some years or values.
"filters": every non-date condition, as {{"column": "Table[Column]", "values": \
[exact values from VALUES OF TEXT COLUMNS]}}; a measure threshold as \
{{"measure": name, "operator": "gt|gte|lt|lte", "value": number}}.
"years": the calendar years to include, as numbers, when the period is whole \
years; "date_from" / "date_to": "YYYY-MM-DD" when it is a span of months, \
weeks or days. Write the resolved years or dates here, never relative words. \
Leave them empty when no period is asked.
"sort": {{"by": measure or Table[Column], "direction": "asc|desc"}} or null; \
"top": N for a top/bottom N, else null.
"growth": only when they explicitly ask how something grew, changed, increased \
or declined: {{"mode": "range" (between two years) or "yoy" (each year against \
the year before), "years": [first, last] or null, "direction": "desc" when the \
highest growth comes first (most, fastest, best), "asc" when the lowest or \
biggest decline comes first}}; otherwise null. Do not repeat these years in \
"years".
"share": only when they ask for a share, percentage, mix or contribution of a \
total: {{"value": the exact value whose share is wanted (what % of sales came \
from X -> X), or null for the share of each group}}; that value's column goes \
in "breakdowns", not "filters". Otherwise null.
"holiday": only for Ramadan, Eid al-Fitr, Saudi National Day or White Friday: \
{{"name": one of {list(HOLIDAYS)}, "year": the year or null}}; otherwise null.
"chart": the chart type the user asked for (column, bar, line, pie or donut), \
else null.
"assumptions": short notes on every reading the user might not expect \
(mapped words, resolved dates, defaults).
"out_of_scope": true only when the message has nothing to do with this \
business data at all (for example the weather or a joke); false otherwise."""


def _parse(raw):
    text = str(raw or "").strip()
    match = re.search(r"\{.*\}", text, re.S)
    data = json.loads(match.group(0) if match else text)
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


_OPERATORS = {"gt": "above", "gte": "at least", "lt": "below", "lte": "at most"}
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _names(items):
    return [str(i).strip() for i in items or [] if str(i).strip()]


def _join(words):
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def compose(data):
    """The planner's request, written from the reading's explicit fields
    (a plain "request" sentence is used as it is)."""

    if str(data.get("request") or "").strip():
        return str(data["request"]).strip()
    measures = _names(data.get("measures")) or ["Net Sales"]
    text = _join(measures)
    breakdowns = _names(data.get("breakdowns"))
    if breakdowns:
        text += " by " + _join(breakdowns)
    conditions = []
    for item in data.get("filters") or []:
        if not isinstance(item, dict):
            continue
        if item.get("column") and _names(item.get("values")):
            values = _names(item["values"])
            conditions.append(f"{item['column']} is "
                              + " or ".join(f'"{v}"' for v in values))
        elif item.get("measure") and item.get("operator") in _OPERATORS:
            conditions.append(f"{item['measure']} {_OPERATORS[item['operator']]} "
                              f"{item.get('value')}")
    years = sorted({y for y in (_year(v) for v in data.get("years") or []) if y})
    if isinstance(data.get("growth"), dict):
        years = []  # the growth calculation sets its own years
    if len(years) > 1 and years == list(range(years[0], years[-1] + 1)):
        conditions.append(f"DimDate[Year] from {years[0]} to {years[-1]}")
    elif years:
        conditions.append("DimDate[Year] " + " or ".join(map(str, years)))
    start, end = (str(data.get(k) or "").strip() for k in ("date_from", "date_to"))
    if _DAY.match(start) and _DAY.match(end):
        conditions.append(f"DimDate[Date] from {start} to {end}")
    elif _DAY.match(start):
        conditions.append(f"DimDate[Date] from {start}")
    elif _DAY.match(end):
        conditions.append(f"DimDate[Date] up to {end}")
    if conditions:
        text += ", where " + " and ".join(conditions)
    sort = data.get("sort") if isinstance(data.get("sort"), dict) else {}
    if not isinstance(sort.get("by"), str):
        sort = {}
    top = data.get("top")
    if isinstance(data.get("growth"), dict) or isinstance(data.get("share"), dict):
        # Ranking a change or a share happens after the query.
        sort, top = {}, None
    direction = "descending" if str(sort.get("direction")).lower() != "asc" else "ascending"
    if isinstance(top, int) and not isinstance(top, bool) and top > 0:
        text += (f", top {top} by {sort.get('by') or measures[0]} "
                 f"({direction})")
    elif sort.get("by"):
        text += f", sorted by {sort['by']} {direction}"
    return text


def readable(request):
    """The request for people: "DimProduct[ProductName]" -> "product name"."""

    def words(match):
        return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", match.group(1)).lower()

    return re.sub(r"\b\w+\[([^\]]+)\]", words, request)


def understand(question, chat, model, today=None):
    """Return the reading of ``question``:
    ``{"request", "assumptions", "chart", "spec", "out_of_scope"}``.
    ``spec`` is the growth / share / holiday-period dict for
    ``src.analysis.adjust_plan`` (or ``None``). Raises ``ValueError`` when
    the model's reply cannot be read (the caller then plans the question as
    written)."""

    today = today or dt.date.today()
    messages = [{"role": "system", "content": instructions(today)},
                {"role": "user", "content": str(question)}]
    try:
        data = _parse(chat(model, messages))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"unreadable understanding reply: {exc}") from exc
    if data.get("out_of_scope"):
        request = ""
    elif not (data.get("request") or _names(data.get("measures"))
              or _names(data.get("breakdowns")) or data.get("filters")):
        raise ValueError("the understanding reply names nothing to query")
    else:
        request = compose(data)
    assumptions = [str(a).strip().rstrip(".") for a in data.get("assumptions") or []
                   if str(a).strip().rstrip(".")]
    chart = CHARTS.get(str(data.get("chart") or "").strip().lower())
    spec, notes = to_spec(data, today)
    return {"request": request, "assumptions": assumptions + notes,
            "chart": chart, "spec": spec,
            "out_of_scope": bool(data.get("out_of_scope"))}


def _year(value):
    try:
        year = int(str(value).strip()[:4])
    except (TypeError, ValueError):
        return None
    return year if 1900 < year < 2200 else None


def to_spec(data, today):
    """The structured growth / share / holiday fields as the spec used by
    ``src.analysis``. Returns ``(spec or None, notes)``."""

    from src.analysis import RAMADAN, SHARE_COLUMNS, period_range
    from evaluation.semantic_checks import load_catalog

    spec, notes = {}, []

    holiday = data.get("holiday")
    if isinstance(holiday, dict) and holiday.get("name"):
        name = str(holiday["name"]).lower()
        name = next((h for h in HOLIDAYS if h.split()[0] in name), name)
        year = _year(holiday.get("year"))
        if year is None:
            past = [y for y in sorted(RAMADAN, reverse=True)
                    if period_range(name, y) and period_range(name, y)[1] <= today]
            year = past[0] if past else None
        span = period_range(name, year) if year else None
        if span:
            spec["period"] = {"name": name, "year": year,
                              "start": span[0].isoformat(),
                              "end": span[1].isoformat()}
            label = "Eid al-Fitr" if name == "eid" else name.title()
            notes.append(f"{label} {year} is {span[0]:%d %b} to {span[1]:%d %b %Y}")
        else:
            notes.append(f"no dates are known for {name} {year or ''}".strip())

    growth = data.get("growth")
    if isinstance(growth, dict):
        years = [y for y in (_year(v) for v in growth.get("years") or []) if y]
        mode = "yoy" if str(growth.get("mode")).lower() == "yoy" else "range"
        spec["growth"] = {
            "mode": mode,
            "years": sorted(years)[:1] + sorted(years)[-1:] if len(years) >= 2 else None,
            "direction": "asc" if str(growth.get("direction")).lower() == "asc" else "desc"}
        years = spec["growth"]["years"]
        notes.append("growth against the year before" if mode == "yoy" else
                     f"growth from {years[0]} to {years[1]}" if years else
                     "growth from the first to the last year in the data")

    share = data.get("share")
    if isinstance(share, dict) and "growth" not in spec:
        entry = {"value": None, "table": None, "column": None}
        wanted = str(share.get("value") or "").strip().lower()
        if wanted:
            catalog = load_catalog()
            for table, column in SHARE_COLUMNS:
                match = next((v for v in catalog.get(f"{table}[{column}]", [])
                              if v.lower() == wanted), None)
                if match:
                    entry.update(table=table, column=column, value=match)
                    break
        spec["share"] = entry
        notes.append(f"{entry['value']}'s share of each total" if entry["value"]
                     else "each group's share of the total")

    return (spec or None), notes
