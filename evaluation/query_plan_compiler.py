import math
import textwrap

from evaluation.query_plan_validator import validate_query_plan


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

    validate_query_plan(plan)

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

            expressions.append(
                reference
                + " "
                + OPERATORS[condition["operator"]]
                + " "
                + dax_literal(condition["value"])
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
