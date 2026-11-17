"""Reads everyday wording into the words of the data model, before planning.

The planner and the answer checks only know the model's own words. People
say "branch" for store, "revenue" for net sales, "this year" for 2026 and
make typos. This module rewrites those parts of the question with fixed
rules and returns a note for every change, so the answer can say how the
question was read:

    interpret("How much money did each branch make this year?")
    -> ("How much net sales did each store make in 2026?",
        ['"money" as net sales', '"branch" as store', '"this year" as 2026'])

It also supplies:

* ``NEUTRAL_WORDS``: verbs and filler ("make", "earn", "grow") that name no
  quantity, so they do not send an otherwise clear question to the verifier;
* ``merge_repeated_filters``: "compare 2025 and 2026" is planned as two
  filters on the same column, which no row can satisfy; it becomes a
  breakdown by that column;
* ``plan_to_question``: a plain question for a checked plan, offered as a
  suggestion when a question is declined.

Nothing here calls a model.
"""

import calendar
import datetime as dt
import difflib
import re

from evaluation.answerability import build_vocabulary

# ---------------------------------------------------------------- dates


def _month_name(year, month):
    return f"{calendar.month_name[month]} {year}"


def _relative_dates(today):
    year, month = today.year, today.month
    last_month = (year, month - 1) if month > 1 else (year - 1, 12)
    quarter = (month - 1) // 3 + 1
    last_quarter = (year, quarter - 1) if quarter > 1 else (year - 1, 4)
    yesterday = today - dt.timedelta(days=1)
    return [
        (r"(?:this|the current|current) year|year[- ]to[- ]date|\bytd", str(year)),
        (r"(?:last|previous|the previous|the last) year", str(year - 1)),
        (r"(?:this|the current|current) month|month[- ]to[- ]date|\bmtd",
         _month_name(year, month)),
        (r"(?:last|previous|the previous) month", _month_name(*last_month)),
        (r"(?:this|the current|current) quarter", f"Q{quarter} {year}"),
        (r"(?:last|previous|the previous) quarter",
         f"Q{last_quarter[1]} {last_quarter[0]}"),
        (r"today", today.isoformat()),
        (r"yesterday", yesterday.isoformat()),
    ]


# ------------------------------------------------------------- synonyms
# (pattern, replacement, note label). Applied in order, whole words only.
# A replacement is only made when the wording is not already a model term.

_NOT_AFTER_QUALIFIER = r"(?<!net )(?<!gross )(?<!sales )"
SYNONYMS = [
    # performance words without a metric
    (r"how (?:did|do|does|are|is|were|was) (?:we|it|they|each \w+|every \w+|the \w+) (?:do|doing|perform|performing)",
     "what were net sales", '"how did … do" as net sales'),
    (r"perform(?:s|ed|ing)? (?:the )?(?:best|better|strongest)",
     "have the highest net sales", '"perform best" as highest net sales'),
    (r"perform(?:s|ed|ing)? (?:the )?(?:worst|worse|weakest)",
     "have the lowest net sales", '"perform worst" as lowest net sales'),
    (r"(?:best|top|strongest)[- ]performing", "highest net sales",
     '"best performing" as highest net sales'),
    (r"(?:worst|weakest|poorest)[- ]performing", "lowest net sales",
     '"worst performing" as lowest net sales'),
    (r"performance", "net sales", '"performance" as net sales'),
    # money words
    (_NOT_AFTER_QUALIFIER + r"(?:sales )?(?:revenues?|turnover|income|earnings|takings|money|sales value)",
     "net sales", None),
    (r"profitability", "gross margin %", '"profitability" as gross margin %'),
    (r"(?:gross )?profit margins?", "gross margin %", '"profit margin" as gross margin %'),
    # quantities
    (r"(?:pieces|qty|volume|volumes)(?: sold)?", "units sold", None),
    # counting orders: the planner otherwise counts units
    (r"(?:how many|number of|count of|total number of|total|the)\s+orders"
     r"|^orders(?= by| per| in| for)",
     "number of distinct orders", '"orders" as number of distinct orders'),
    # places and people
    (r"branch(?:es)?|shops?|outlets?|locations?|sites?|branch", "store", None),
    (r"clients?", "customer", None),
    # time
    (r"periods?", "year and month", '"period" as year and month'),
    (r"weekdays?|days? of (?:the )?week", "day name", '"day of week" as day name'),
    (r"over time|trend(?:s)? over time", "by month", '"over time" as by month'),
    # Plain "sales" is read the same way every time (the planner otherwise
    # picks Net Sales or Gross Sales at random). Not in "sales channel",
    # "sales target", "how many sales" ...
    (r"(?<!net )(?<!gross )(?<!many )(?<!number of )sales"
     r"(?! target| variance| channel| achievement)",
     "net sales", '"sales" as net sales'),
]

# "units per brand" is a breakdown, not a ratio; "units per order" stays.
_PER_GROUP = re.compile(
    r"\bper (?=(?:month|year|quarter|day|week|brand|region|store|city|"
    r"category|subcategory|channel|product|segment|customer segment|"
    r"payment method|store type|age band|gender)s?\b)", re.I)

# Words that name no quantity and no model field. They no longer send a
# question to the verifier on their own.
NEUTRAL_WORDS = set("""
make makes made making earn earns earned earning generate generates
generated generating bring brings brought perform performs performed
performing performance doing done go goes went gone happen happened grow
grows grew growth grown change changes changed trend trends time times
look looks like see tell know want need chart charts graph graphs plot
visual visualize visualise create draw build please could would should
together combined overall entire across throughout so far since until
past previous current recent latest year's month's how's what's did
""".split())


def _pluralise(replacement, original):
    if replacement in ("store", "customer") and original.lower().endswith("s"):
        return replacement + "s"
    return replacement


def _apply(pattern, replacement, question):
    regex = re.compile(rf"\b(?:{pattern})\b", re.I)
    changes = []

    def substitute(match):
        text = match.group(0)
        new = _pluralise(replacement, text)
        if new.lower() != text.lower():
            changes.append(text)
        return new

    return regex.sub(substitute, question), changes


# ---------------------------------------------------------------- typos

_VOCABULARY = None


def _vocabulary():
    global _VOCABULARY
    if _VOCABULARY is None:
        _VOCABULARY = build_vocabulary() | NEUTRAL_WORDS | {
            "revenue", "turnover", "income", "earnings", "branch", "shop",
            "outlet", "money", "pieces", "profitability", "period", "client",
            "weekday", "performance"}
    return _VOCABULARY


def _fix_typos(question):
    vocabulary = _vocabulary()
    candidates = sorted(word for word in vocabulary if len(word) >= 4)
    notes = []

    def substitute(match):
        word = match.group(0)
        lower = word.lower()
        if len(lower) < 5 or lower in vocabulary or lower.rstrip("s") in vocabulary:
            return word
        close = difflib.get_close_matches(lower, candidates, n=1, cutoff=0.8)
        # "given" is not a typo of "give": another form of a known word.
        if not close or close[0].startswith(lower[:-1]) or lower.startswith(close[0]):
            return word
        notes.append(f'"{word}" as {close[0]}')
        return close[0]

    return re.sub(r"[A-Za-z]+", substitute, question), notes


# ------------------------------------------------------------- interface


def interpret(question, today=None):
    """Return ``(rewritten_question, notes)``. ``notes`` is empty when the
    question was left as it was."""

    today = today or dt.date.today()
    text, notes = _fix_typos(str(question or ""))

    for pattern, replacement in _relative_dates(today):
        text, changed = _apply(pattern, replacement, text)
        notes += [f'"{c}" as {replacement}' for c in changed]

    for pattern, replacement, label in SYNONYMS:
        if replacement is None:
            continue
        text, changed = _apply(pattern, replacement, text)
        if changed:
            notes += [label] if label else [
                f'"{c}" as {_pluralise(replacement, c)}' for c in changed]

    text = re.sub(r"\bnet sales sales\b", "net sales", text, flags=re.I)
    text = re.sub(r"\bnet net sales\b", "net sales", text, flags=re.I)
    text = _PER_GROUP.sub("by ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if text and notes:
        text = text[0].upper() + text[1:]
    return text, list(dict.fromkeys(notes))


_YEAR_MONTH = re.compile(r"^(\d{4})-(\d{1,2})(?:-01)?(?:T.*)?$")
# "2026-Q4", "Q4 2026", "2026 Q4"
_YEAR_QUARTER = re.compile(r"^(?:(\d{4})[- ]?Q([1-4])|Q([1-4])[- ]?(\d{4}))$", re.I)


def _split_year_month(plan):
    """``YearMonth = '2026-10'`` cannot be compared in DAX (the column is a
    date); the same filter as ``Year = 2026`` and ``MonthNumber = 10``."""

    filters = []
    changed = False
    for item in plan.get("filters") or []:
        match = (_YEAR_MONTH.match(str(item.get("value")))
                 if item.get("type") == "column" and item.get("column") == "YearMonth"
                 and item.get("operator") == "eq" else None)
        quarter = (_YEAR_QUARTER.match(str(item.get("value")).strip())
                   if item.get("type") == "column"
                   and item.get("column") in ("YearMonth", "Quarter", "Date")
                   and item.get("operator") == "eq" else None)
        if match:
            changed = True
            filters.append(dict(item, column="Year", value=int(match.group(1))))
            filters.append(dict(item, column="MonthNumber",
                                value=int(match.group(2))))
        elif quarter:
            changed = True
            year = quarter.group(1) or quarter.group(4)
            number = quarter.group(2) or quarter.group(3)
            filters.append(dict(item, table="DimDate", column="Year",
                                value=int(year)))
            filters.append(dict(item, table="DimDate", column="Quarter",
                                value=f"Q{number}"))
        else:
            filters.append(item)
    return (dict(plan, filters=filters), True) if changed else (plan, False)


_ORDERS_ALIAS = re.compile(r"\border(?:s| count)\b", re.I)
ORDER_COUNT = {"kind": "distinct_count", "table": "FactSales", "column": "OrderID"}


def _count_orders(plan):
    """A metric labelled "orders" that computes another measure (the
    planner's repair turn tends to substitute Units Sold) is replaced by
    the distinct count of order IDs."""

    changed = False
    metrics = []
    for metric in plan.get("metrics") or []:
        expression = metric.get("expression") or {}
        if (_ORDERS_ALIAS.search(str(metric.get("alias", "")))
                and expression.get("kind") == "measure"
                and "order" not in expression.get("name", "").lower()
                and "per" not in str(metric.get("alias", "")).lower()):
            metric = dict(metric, expression=dict(ORDER_COUNT))
            changed = True
        metrics.append(metric)
    return (dict(plan, metrics=metrics), True) if changed else (plan, False)


def _dedupe_metrics(plan):
    """After a merge, "Sales 2025" and "Sales 2026" are the same metric."""

    kept, alias_map = [], {}
    for metric in plan.get("metrics") or []:
        same = next((k for k in kept if k["expression"] == metric["expression"]), None)
        if same is None:
            expression = metric["expression"]
            alias = (expression["name"] if expression.get("kind") == "measure"
                     else metric["alias"])
            new = dict(metric, alias=alias)
            kept.append(new)
            alias_map[metric["alias"]] = alias
        else:
            alias_map[metric["alias"]] = same["alias"]
    order_by = []
    for item in plan.get("order_by") or []:
        item = dict(item, field=alias_map.get(item["field"], item["field"]))
        if item not in order_by:
            order_by.append(item)
    filters = [dict(f, alias=alias_map.get(f["alias"], f["alias"]))
               if f.get("type") == "metric" else f
               for f in plan.get("filters") or []]
    return dict(plan, metrics=kept, order_by=order_by, filters=filters)


def normalize_plan(plan):
    """Fix plan shapes that cannot answer what was asked. Returns
    ``(plan, notes)``; the plan is re-validated by the caller."""

    notes = []
    plan, recounted = _count_orders(plan)
    if recounted:
        notes.append("orders are counted as distinct order IDs")
    plan, split = _split_year_month(plan)
    plan, merged = merge_repeated_filters(plan)
    if merged:
        plan = _dedupe_metrics(plan)
        notes.append(merged)
    return plan, notes


def merge_repeated_filters(plan):
    """Turn several "equals" filters on one column (no row can match them
    all) into a breakdown by that column. Returns ``(plan, note or None)``."""

    filters = plan.get("filters") or []
    by_column = {}
    for item in filters:
        if item.get("type") == "column" and item.get("operator") == "eq":
            by_column.setdefault((item["table"], item["column"]), []).append(item)
    repeated = {key for key, items in by_column.items()
                if len({str(i["value"]) for i in items}) > 1}
    if not repeated:
        return plan, None

    plan = dict(plan)
    plan["filters"] = [item for item in filters if not (
        item.get("type") == "column" and item.get("operator") == "eq"
        and (item["table"], item["column"]) in repeated)]
    group_by = list(plan.get("group_by") or [])
    for table, column in sorted(repeated):
        if {"table": table, "column": column} not in group_by:
            group_by.insert(0, {"table": table, "column": column})
    plan["group_by"] = group_by
    columns = ", ".join(column for _, column in sorted(repeated))
    return plan, f"the values being compared are shown side by side (by {columns})"


_COLUMN_WORDS = {
    "YearMonth": "month", "MonthName": "month name", "MonthNumber": "month",
    "DayName": "day name", "StoreName": "store", "ProductName": "product",
    "CustomerSegment": "customer segment", "StoreType": "store type",
    "PaymentMethod": "payment method", "AgeBand": "age band",
}


def _column_words(column):
    if column in _COLUMN_WORDS:
        return _COLUMN_WORDS[column]
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", column).lower()


def plan_to_question(plan):
    """A plain question that asks for exactly what the plan computes, or
    ``None`` when a part of it has no simple wording."""

    metrics = []
    for metric in plan.get("metrics") or []:
        expression = metric.get("expression") or {}
        if expression.get("kind") == "measure":
            metrics.append(expression["name"].lower().replace(" %", " %"))
        elif expression.get("kind") == "distinct_count":
            metrics.append(f"number of distinct {_column_words(expression['column'])}s")
        else:
            return None
    if not metrics:
        return None
    text = " and ".join(metrics)
    text = text[0].upper() + text[1:]
    groups = [_column_words(g["column"]) for g in plan.get("group_by") or []]
    if groups:
        text += " by " + " and ".join(groups)
    for item in plan.get("filters") or []:
        if item.get("type") != "column" or item.get("operator") not in ("eq", "ne"):
            return None
        word = "for" if item["operator"] == "eq" else "excluding"
        value = item["value"]
        if item["column"] in ("Year", "Quarter", "MonthName", "YearMonth"):
            text += f" in {value}" if item["operator"] == "eq" else f" excluding {value}"
        else:
            text += f" {word} {_column_words(item['column'])} {value}"
    if plan.get("limit"):
        order = (plan.get("order_by") or [{}])[0]
        side = "top" if order.get("direction", "desc") == "desc" else "bottom"
        text = f"{side.capitalize()} {plan['limit']}: " + text[0].lower() + text[1:]
    return text
