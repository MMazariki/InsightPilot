import re

from evaluation.result_comparator import compare_results


def compare_results_with_metric_alias(expected, actual, **tolerances):
    """Allow one renamed metric while preserving strict value comparison."""

    strict = compare_results(expected, actual, **tolerances)

    if strict["reason"] != "Column mismatch":
        return strict

    missing = set(expected["columns"]) - set(actual["columns"])
    extra = set(actual["columns"]) - set(expected["columns"])

    if len(missing) != 1 or len(extra) != 1:
        return strict

    expected_metric = next(iter(missing))
    actual_metric = next(iter(extra))

    # Only accept unqualified metric aliases, not dimension columns.
    metric_pattern = r"\[[^\[\]]+\]"

    if not all(
        re.fullmatch(metric_pattern, name)
        for name in (expected_metric, actual_metric)
    ):
        return strict

    if any(expected_metric not in row for row in expected["rows"]):
        return strict

    if any(actual_metric not in row for row in actual["rows"]):
        return strict

    remapped = {
        "columns": [
            expected_metric if col == actual_metric else col
            for col in actual["columns"]
        ],
        "rows": [
            {
                expected_metric if key == actual_metric else key: value
                for key, value in row.items()
            }
            for row in actual["rows"]
        ],
    }

    result = compare_results(expected, remapped, **tolerances)

    if result["passed"]:
        result["reason"] = "Results equivalent (metric alias differs)"
        result["alias_mapping"] = {
            actual_metric: expected_metric
        }

    return result
