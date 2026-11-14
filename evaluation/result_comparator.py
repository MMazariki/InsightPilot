import math
from collections import Counter


def compare_results(expected, actual, relative_tolerance=1e-6, absolute_tolerance=0.01):
    """Compare Power BI result sets without depending on row order."""

    expected_columns = set(expected["columns"])
    actual_columns = set(actual["columns"])

    if expected_columns != actual_columns:
        return {
            "passed": False,
            "reason": "Column mismatch",
            "missing_columns": sorted(expected_columns - actual_columns),
            "extra_columns": sorted(actual_columns - expected_columns),
        }

    expected_rows = expected["rows"]
    actual_rows = actual["rows"]

    if len(expected_rows) != len(actual_rows):
        return {
            "passed": False,
            "reason": "Row count mismatch",
            "expected_count": len(expected_rows),
            "actual_count": len(actual_rows),
        }

    columns = sorted(expected_columns)

    def equivalent_value(a, b):
        if isinstance(a, bool) or isinstance(b, bool):
            return type(a) is type(b) and a == b

        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return math.isclose(
                a, b,
                rel_tol=relative_tolerance,
                abs_tol=absolute_tolerance,
            )

        return a == b

    unmatched = list(actual_rows)

    for expected_row in expected_rows:
        match_index = next(
            (
                index
                for index, actual_row in enumerate(unmatched)
                if all(
                    equivalent_value(expected_row.get(column), actual_row.get(column))
                    for column in columns
                )
            ),
            None,
        )

        if match_index is None:
            return {
                "passed": False,
                "reason": "Row value mismatch",
                "expected_row": expected_row,
            }

        unmatched.pop(match_index)

    return {
        "passed": True,
        "reason": "Results equivalent",
        "row_count": len(expected_rows),
    }
