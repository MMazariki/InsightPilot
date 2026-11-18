import math
import re
import textwrap

from evaluation.query_plan_validator import load_model, validate_query_plan


OPERATORS = {
    "eq": "=",
    "ne": "<>",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}


def field_reference(table, column):
    return f"'{table}'[{column}]"


def dax_literal(value):
    if isinstance(value, bool):
        return "TRUE()" if value else "FALSE()"

    if isinstance(value, str):
        return '"' + value.replace('"', '""') + '"'

    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError("Non-finite numeric values are not supported.")
        return str(value)

    raise ValueError(f"Unsupported literal type: {type(value).__name__}")


_DATE_TEXT = re.compile(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?(?:[T ]00:00(?::00)?)?$")
_DATE_COLUMNS = None


def _date_columns():
    global _DATE_COLUMNS
    if _DATE_COLUMNS is None:
        _DATE_COLUMNS = {
            (table["Name"], column["Name"])
            for table in load_model()["Tables"]
            for column in table.get("Columns", [])
            if column.get("Type") == "DateTime"
        }
    return _DATE_COLUMNS


def column_literal(table, column, value):
    """Like ``dax_literal``, but a date written as text ("2026-11-16",
    "2026-10") becomes ``DATE(...)`` when the column holds dates: DAX cannot
    compare a date column with text."""

    if isinstance(value, str) and (table, column) in _date_columns():
        match = _DATE_TEXT.match(value.strip())
        if match:
            year, month, day = match.group(1), match.group(2), match.group(3) or 1
            return f"DATE({int(year)}, {int(month)}, {int(day)})"
    return dax_literal(value)


def compile_expression(expression):
    kind = expression["kind"]

    if kind == "measure":
        return f"[{expression['name']}]"

    if kind == "distinct_count":
        field = field_reference(
            expression["table"],
            expression["column"],
        )
        return f"DISTINCTCOUNT({field})"

    if kind == "divide":
        numerator = compile_expression(expression["numerator"])
        denominator = compile_expression(expression["denominator"])
        return f"DIVIDE({numerator}, {denominator})"

    raise ValueError(f"Unsupported expression: {kind}")


def compile_query_plan(plan):
    """Compile a validated structured query plan into one DAX result table."""

    # The planner's own plans were already checked against the plain
    # contract; the app's plan fixes may add an ``in`` column filter.
    validate_query_plan(plan, allow_in=True)

    metrics = plan["metrics"]
    dimensions = plan["group_by"]
    filters = plan["filters"]
    sorting = plan["order_by"]
    limit = plan["limit"]

    if limit is not None and not sorting:
        raise ValueError("A result limit requires explicit sorting.")

    metric_items = []

    for metric in metrics:
        alias = dax_literal(metric["alias"])
        expression = compile_expression(metric["expression"])
        metric_items.append(f"{alias}, {expression}")

    if dimensions:
        items = [
            field_reference(field["table"], field["column"])
            for field in dimensions
        ]
        items.extend(metric_items)

        table_expression = (
            "SUMMARIZECOLUMNS(\n    "
            + ",\n    ".join(items)
            + "\n)"
        )
    else:
        table_expression = (
            "ROW(\n    "
            + ",\n    ".join(metric_items)
            + "\n)"
        )

    column_filters = [
        condition for condition in filters
        if condition["type"] == "column"
    ]

    if column_filters:
        expressions = []

        for condition in column_filters:
            reference = field_reference(
                condition["table"],
                condition["column"],
            )

            if condition["operator"] == "in":
                values = ", ".join(
                    column_literal(condition["table"], condition["column"], v)
                    for v in condition["value"])
                expressions.append(f"{reference} IN {{{values}}}")
                continue
            expressions.append(
                reference
                + " "
                + OPERATORS[condition["operator"]]
                + " "
                + column_literal(condition["table"], condition["column"],
                                 condition["value"])
            )

        table_expression = (
            "CALCULATETABLE(\n"
            + textwrap.indent(table_expression, "    ")
            + ",\n    "
            + ",\n    ".join(expressions)
            + "\n)"
        )

    metric_filters = [
        condition for condition in filters
        if condition["type"] == "metric"
    ]

    if metric_filters:
        predicates = []

        for condition in metric_filters:
            predicates.append(
                f"[{condition['alias']}] "
                + OPERATORS[condition["operator"]]
                + " "
                + dax_literal(condition["value"])
            )

        table_expression = (
            "FILTER(\n"
            + textwrap.indent(table_expression, "    ")
            + ",\n    "
            + " && ".join(predicates)
            + "\n)"
        )

    group_references = {
        f"{field['table']}[{field['column']}]":
        field_reference(field["table"], field["column"])
        for field in dimensions
    }

    def sorting_reference(name):
        return group_references.get(name, f"[{name}]")

    sort_items = [
        (
            sorting_reference(item["field"]),
            item["direction"].upper(),
        )
        for item in sorting
    ]

    if limit is not None:
        used = {item[0] for item in sort_items}

        for reference in group_references.values():
            if reference not in used:
                sort_items.append((reference, "ASC"))

        arguments = [
            f"{reference}, {direction}"
            for reference, direction in sort_items
        ]

        table_expression = (
            f"TOPN(\n    {limit},\n"
            + textwrap.indent(table_expression, "    ")
            + ",\n    "
            + ",\n    ".join(arguments)
            + "\n)"
        )

    dax = "EVALUATE\n" + table_expression

    if sort_items:
        dax += "\nORDER BY " + ", ".join(
            f"{reference} {direction}"
            for reference, direction in sort_items
        )

    return dax
