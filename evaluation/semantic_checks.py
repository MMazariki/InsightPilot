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


def _expressions(expression):
    if not isinstance(expression, dict):
        return
    yield expression
    if expression.get("kind") == "divide":
        yield from _expressions(expression.get("numerator"))
        yield from _expressions(expression.get("denominator"))


def check_reference_hints(plan, model=None):
    """Rule 0 (pipeline v6): fix-it messages for misplaced references.

    Only references that exist elsewhere in the model get a "use X instead"
    hint. A measure that does not exist anywhere keeps clarification as the
    suggested path, so undefined metrics are not pushed into substitutes.
    """

    model = load_model() if model is None else model
    tables = {
        table["Name"]: {column["Name"] for column in table.get("Columns", [])}
        for table in model["Tables"]
    }
    measures = sorted(
        measure["Name"]
        for table in model["Tables"]
        for measure in table.get("Measures", [])
    )
    owners = {}
    for table, columns in tables.items():
        for column in columns:
            owners.setdefault(column.casefold(), []).append(f"{table}[{column}]")

    def column_hint(table, column):
        if table in tables and column in tables[table]:
            return
        found = owners.get(str(column).casefold())
        if found:
            raise ValueError(
                f"{table}[{column}] does not exist. The column {column} is "
                f"{' / '.join(found)}; reference it with that table."
            )

    for metric in plan.get("metrics", []) or []:
        if not isinstance(metric, dict):
            continue
        for expression in _expressions(metric.get("expression")):
            kind = expression.get("kind")
            if kind == "measure":
                name = expression.get("name")
                if name in measures:
                    continue
                found = owners.get(str(name).casefold())
                if found:
                    raise ValueError(
                        f"'{name}' is not a measure; it is the column "
                        f"{' / '.join(found)}. Use it in group_by or in a "
                        f"column filter, and use an existing measure as the "
                        f"metric (measures: {', '.join(measures)})."
                    )
                raise ValueError(
                    f"No measure named '{name}' exists. Available measures: "
                    f"{', '.join(measures)}. If the question needs a metric "
                    f"the model does not contain, return a clarification."
                )
            if kind == "distinct_count":
                column_hint(expression.get("table"), expression.get("column"))

    group_refs = {}
    for item in plan.get("group_by", []) or []:
        if isinstance(item, dict):
            column_hint(item.get("table"), item.get("column"))
            group_refs[str(item.get("column")).casefold()] = (
                f"{item.get('table')}[{item.get('column')}]")
    for condition in plan.get("filters", []) or []:
        if isinstance(condition, dict) and condition.get("type") == "column":
            column_hint(condition.get("table"), condition.get("column"))

    aliases = {
        metric.get("alias") for metric in plan.get("metrics", []) or []
        if isinstance(metric, dict)
    }
    for item in plan.get("order_by", []) or []:
        if not isinstance(item, dict):
            continue
        field = item.get("field")
        if field in aliases or field in group_refs.values():
            continue
        qualified = group_refs.get(str(field).casefold())
        if qualified:
            raise ValueError(
                f"Sort field '{field}' must be written as {qualified}."
            )

    return plan


def check_semantic_rules(plan, catalog=None, model=None,
                         reject_single_value_grouping=False,
                         reference_hints=False):
    """Raise ``ValueError`` with an actionable message, else return plan.

    ``reject_single_value_grouping`` enables rule 3 (pipeline v5): a column
    that is filtered to one value with ``eq`` must not also be grouped by,
    because the grouping only adds a constant column. ``ne`` filters are
    unaffected ("by channel, excluding Delivery" is legitimate).
    ``reference_hints`` enables rule 0 (pipeline v6), run first.
    """

    catalog = load_catalog() if catalog is None else catalog
    model = load_model() if model is None else model

    if reference_hints:
        check_reference_hints(plan, model)

    # Rule 3 (optional): grouping by a column filtered to a single value.
    if reject_single_value_grouping:
        grouped = {
            f"{item.get('table')}[{item.get('column')}]"
            for item in (plan.get("group_by", []) or [])
            if isinstance(item, dict)
        }
        for condition in plan.get("filters", []) or []:
            if (not isinstance(condition, dict)
                    or condition.get("type") != "column"
                    or condition.get("operator") != "eq"):
                continue
            field = f"{condition.get('table')}[{condition.get('column')}]"
            if field in grouped:
                raise ValueError(
                    f"The plan groups by {field} while filtering it to the "
                    f"single value {condition.get('value')!r}, which only adds "
                    f"a constant column. Remove {field} from group_by and keep "
                    f"the filter, unless the user asked to compare several "
                    f"{condition.get('column')} values."
                )

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
