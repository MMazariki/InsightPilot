import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "evaluation"

# Committed, sanitised snapshot of the semantic-model schema (tables, columns,
# measures, relationships only; no workspace/model identifiers). The live
# probe output (fabric_iq_schema.json) is gitignored; a drift test compares
# the two whenever the live file is present.
MODEL_SCHEMA_FILE = EVAL / "semantic_model_schema.json"


def load_model():
    return json.loads(MODEL_SCHEMA_FILE.read_text(encoding="utf-8-sig"))


def validate_query_plan(plan):
    """Validate query-plan structure and semantic-model references."""

    contract = json.loads(
        (EVAL / "query_plan_schema.json").read_text(encoding="utf-8-sig")
    )

    validator = Draft202012Validator(contract)
    errors = sorted(
        validator.iter_errors(plan),
        key=lambda error: str(error.path),
    )

    if errors:
        error = errors[0]
        location = ".".join(map(str, error.absolute_path)) or "root"
        raise ValueError(f"Invalid query plan at {location}: {error.message}")

    model = load_model()

    tables = {
        table["Name"]: {
            column["Name"]: column["Type"]
            for column in table.get("Columns", [])
        }
        for table in model["Tables"]
    }

    measures = {
        measure["Name"]
        for table in model["Tables"]
        for measure in table.get("Measures", [])
    }

    def check_column(table, column):
        if table not in tables:
            raise ValueError(f"Unknown table: {table}")

        if column not in tables[table]:
            raise ValueError(f"Unknown column: {table}[{column}]")

        return tables[table][column]

    def check_expression(expression):
        kind = expression["kind"]

        if kind == "measure":
            if expression["name"] not in measures:
                raise ValueError(
                    f"Unknown measure: {expression['name']}"
                )

        elif kind == "distinct_count":
            check_column(expression["table"], expression["column"])

        elif kind == "divide":
            check_expression(expression["numerator"])
            check_expression(expression["denominator"])

    # DAX object names are case-insensitive, so name collisions are checked
    # on case-folded names.
    aliases = set()
    folded_aliases = set()

    for metric in plan["metrics"]:
        alias = metric["alias"]

        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9 _%/-]{0,79}", alias):
            raise ValueError(f"Unsafe or invalid metric alias: {alias}")

        if alias.casefold() in folded_aliases:
            raise ValueError(f"Duplicate metric alias: {alias}")

        aliases.add(alias)
        folded_aliases.add(alias.casefold())
        check_expression(metric["expression"])

    grouping_fields = set()

    for field in plan["group_by"]:
        table = field["table"]
        column = field["column"]

        check_column(table, column)

        reference = f"{table}[{column}]"

        if reference in grouping_fields:
            raise ValueError(f"Duplicate grouping field: {reference}")

        # SUMMARIZECOLUMNS rejects an added column whose name matches a
        # grouping column ("cannot add column [X] since it already exists").
        if column.casefold() in folded_aliases:
            raise ValueError(
                f"Metric alias collides with grouping column: {reference}"
            )

        grouping_fields.add(reference)

    for condition in plan["filters"]:
        operator = condition["operator"]
        value = condition["value"]

        if condition["type"] == "metric":
            if condition["alias"] not in aliases:
                raise ValueError(
                    f"Unknown metric alias: {condition['alias']}"
                )
            continue

        column_type = check_column(
            condition["table"], condition["column"]
        )

        if column_type == "Boolean":
            valid = type(value) is bool and operator in ("eq", "ne")
        elif column_type in ("Integer", "Double", "Decimal"):
            valid = type(value) in (int, float)
        elif column_type in ("Text", "DateTime"):
            valid = isinstance(value, str)
        else:
            valid = False

        if not valid:
            raise ValueError(
                f"Invalid filter value/operator for "
                f"{condition['table']}[{condition['column']}]"
            )

    for item in plan["order_by"]:
        field = item["field"]

        if field not in aliases and field not in grouping_fields:
            raise ValueError(f"Invalid sorting field: {field}")

    return plan
