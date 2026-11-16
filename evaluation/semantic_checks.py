"""Deterministic semantic checks for query plans (beyond schema validity).

Isolated from ``query_plan_validator`` so earlier evaluation arms keep their
original validation behaviour. Every rejection message is actionable, so it
can be shown to the planner for a single bounded repair attempt.

Rules:
1. Text-column filter values must exist in the committed value catalog
   (``semantic_value_catalog.json``); comparison is case-insensitive, like
   DAX string equality. Non-text values on catalogued columns are rejected
   with the allowed values listed.
2. A distinct count of a dimension-table column is rejected when the plan
   also groups or filters by a column from another table: dimension tables
   are only filtered by their own columns (single-direction relationships),
   so the count would silently ignore that context. The message names the
   FactSales foreign key to count instead.
"""

import json
import re
from pathlib import Path

from evaluation.query_plan_validator import load_model

EVAL = Path(__file__).resolve().parent
CATALOG_FILE = EVAL / "semantic_value_catalog.json"
REFERENCE = re.compile(r"'([^']+)'\[([^\]]+)\]")


def load_catalog():
    return json.loads(CATALOG_FILE.read_text(encoding="utf-8-sig"))["columns"]


def dimension_foreign_keys(model):
    """Map dimension table -> {fact table: fact foreign-key column}."""

    mapping = {}
    for relationship in model.get("ActiveRelationships", []):
        pk = REFERENCE.fullmatch(relationship["PK"])
        fk = REFERENCE.fullmatch(relationship["FK"])
        if pk and fk:
            mapping.setdefault(pk.group(1), {})[fk.group(1)] = fk.group(2)
    return mapping


def _distinct_counts(expression):
    if not isinstance(expression, dict):
        return
    kind = expression.get("kind")
    if kind == "distinct_count":
        yield expression
    elif kind == "divide":
        yield from _distinct_counts(expression.get("numerator"))
        yield from _distinct_counts(expression.get("denominator"))


def check_semantic_rules(plan, catalog=None, model=None):
    """Raise ``ValueError`` with an actionable message, else return plan."""

    catalog = load_catalog() if catalog is None else catalog
    model = load_model() if model is None else model

    # Rule 1: filter values must exist.
    for condition in plan.get("filters", []) or []:
        if not isinstance(condition, dict) or condition.get("type") != "column":
            continue
        field = f"{condition.get('table')}[{condition.get('column')}]"
        allowed = catalog.get(field)
        if allowed is None:
            continue
        value = condition.get("value")
        folded = {item.casefold() for item in allowed}
        if not isinstance(value, str) or value.casefold() not in folded:
            raise ValueError(
                f"Unknown value {value!r} for {field}. "
                f"Allowed values: {', '.join(allowed)}"
            )

    # Rule 2: dimension distinct counts must not ignore other-table context.
    dimensions = dimension_foreign_keys(model)
    context_tables = {
        item.get("table")
        for item in (plan.get("group_by", []) or [])
        if isinstance(item, dict)
    } | {
        item.get("table")
        for item in (plan.get("filters", []) or [])
        if isinstance(item, dict) and item.get("type") == "column"
    }

    for metric in plan.get("metrics", []) or []:
        if not isinstance(metric, dict):
            continue
        for expression in _distinct_counts(metric.get("expression")):
            table = expression.get("table")
            if table not in dimensions:
                continue
            others = sorted(t for t in context_tables if t and t != table)
            if not others:
                continue
            suggestions = ", ".join(
                f"{fact}[{column}]"
                for fact, column in sorted(dimensions[table].items())
            )
            raise ValueError(
                f"DISTINCTCOUNT({table}[{expression.get('column')}]) ignores "
                f"groupings/filters on {', '.join(others)} because {table} is "
                f"not filtered by them. To count {table} entities that had "
                f"sales, use a distinct count of {suggestions}."
            )

    return plan


def describe_catalog(catalog=None):
    """Prompt text listing allowed values per catalogued column."""

    catalog = load_catalog() if catalog is None else catalog
    return "\n".join(
        f"{field}: {', '.join(values)}"
        for field, values in sorted(catalog.items())
    )
