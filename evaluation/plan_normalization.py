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
