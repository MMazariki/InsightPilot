"""Deterministic, semantics-preserving plan normalisation (pipeline v6).

Only display names are touched: metric aliases are result column labels and
never change what is computed. Every change is returned so it can be traced.
"""

import copy
import re

ALLOWED_ALIAS = re.compile(r"[A-Za-z][A-Za-z0-9 _%/-]{0,79}")


def _clean(alias):
    text = re.sub(r"[^A-Za-z0-9 _%/-]", " ", str(alias))
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^[^A-Za-z]+", "", text)
    return text[:80].rstrip() or "Metric"


def sanitize_aliases(plan):
    """Return ``(plan_copy, changes)`` with invalid metric aliases cleaned.

    References to renamed aliases in metric filters and order_by are updated.
    Malformed plans are returned unchanged for the validator to reject.
    """

    if not isinstance(plan, dict) or not isinstance(plan.get("metrics"), list):
        return plan, []

    result = copy.deepcopy(plan)
    taken = {
        metric.get("alias") for metric in result["metrics"]
        if isinstance(metric, dict)
        and isinstance(metric.get("alias"), str)
        and ALLOWED_ALIAS.fullmatch(metric["alias"])
    }
    renamed = {}

    for metric in result["metrics"]:
        if not isinstance(metric, dict) or not isinstance(metric.get("alias"), str):
            continue
        alias = metric["alias"]
        if ALLOWED_ALIAS.fullmatch(alias):
            continue
        base = _clean(alias)
        candidate, suffix = base, 2
        while candidate in taken:
            candidate = f"{base[:76]} {suffix}"
            suffix += 1
        taken.add(candidate)
        renamed[alias] = candidate
        metric["alias"] = candidate

    if renamed:
        for condition in result.get("filters", []) or []:
            if (isinstance(condition, dict) and condition.get("type") == "metric"
                    and condition.get("alias") in renamed):
                condition["alias"] = renamed[condition["alias"]]
        for item in result.get("order_by", []) or []:
            if isinstance(item, dict) and item.get("field") in renamed:
                item["field"] = renamed[item["field"]]

    changes = [
        {"kind": "alias_sanitized", "from": old, "to": new}
        for old, new in renamed.items()
    ]
    return result, changes


COLUMN_LIKE_KINDS = {"column", "dimension", "field", "attribute"}


def drop_grouped_column_metrics(plan):
    """Return ``(plan_copy, changes)`` without metrics that only echo a
    grouping column (pipeline v10).

    Models sometimes list a grouping column again as a metric, e.g.
    ``{"kind": "column", "table": "FactSales", "column": "Channel"}`` while
    also grouping by FactSales[Channel]. The grouping column is already in
    the result, so removing the echo does not change what is computed. Sort
    items that referenced the echo's alias are pointed at the grouping
    column. Nothing is removed if it would leave no metrics.
    """

    if not isinstance(plan, dict) or not isinstance(plan.get("metrics"), list):
        return plan, []

    grouped = {
        (item.get("table"), item.get("column"))
        for item in plan.get("group_by", []) or []
        if isinstance(item, dict)
    }

    def is_echo(metric):
        expression = metric.get("expression") if isinstance(metric, dict) else None
        return (isinstance(expression, dict)
                and expression.get("kind") in COLUMN_LIKE_KINDS
                and (expression.get("table"), expression.get("column")) in grouped)

    echoes = [metric for metric in plan["metrics"] if is_echo(metric)]
    if not echoes or len(echoes) == len(plan["metrics"]):
        return plan, []

    result = copy.deepcopy(plan)
    result["metrics"] = [metric for metric in result["metrics"]
                         if not is_echo(metric)]
    redirected = {
        metric.get("alias"):
        f"{metric['expression']['table']}[{metric['expression']['column']}]"
        for metric in echoes
    }
    for item in result.get("order_by", []) or []:
        if isinstance(item, dict) and item.get("field") in redirected:
            item["field"] = redirected[item["field"]]

    changes = [
        {"kind": "grouped_column_metric_dropped", "alias": alias,
         "column": column}
        for alias, column in redirected.items()
    ]
    return result, changes
