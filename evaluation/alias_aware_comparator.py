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


MAX_RENAMED_METRICS = 4


def compare_results_with_metric_aliases(expected, actual, **tolerances):
    """Allow any number (<= 4) of renamed metrics; values stay strict.

    Generalises ``compare_results_with_metric_alias`` (kept unchanged for
    previously scored reports). Only unqualified metric columns ``[Name]``
    may be renamed; dimension columns ``Table[Column]`` must match exactly.
    Every bijection between missing and extra metric columns is tried, and
    the first one under which all values match is accepted.
    """

    from itertools import permutations

    strict = compare_results(expected, actual, **tolerances)

    if strict["reason"] != "Column mismatch":
        return strict

    missing = sorted(set(expected["columns"]) - set(actual["columns"]))
    extra = sorted(set(actual["columns"]) - set(expected["columns"]))

    if not missing or len(missing) != len(extra):
        return strict

    if len(missing) > MAX_RENAMED_METRICS:
        return strict

    metric_pattern = r"\[[^\[\]]+\]"

    if not all(re.fullmatch(metric_pattern, name) for name in missing + extra):
        return strict

    for ordering in permutations(extra):
        mapping = dict(zip(ordering, missing))
        remapped = {
            "columns": [mapping.get(col, col) for col in actual["columns"]],
            "rows": [
                {mapping.get(key, key): value for key, value in row.items()}
                for row in actual["rows"]
            ],
        }
        result = compare_results(expected, remapped, **tolerances)

        if result["passed"]:
            result["reason"] = (
                "Results equivalent (metric alias differs)"
                if len(mapping) == 1
                else f"Results equivalent ({len(mapping)} metric aliases differ)"
            )
            result["alias_mapping"] = mapping
            return result

    first = dict(zip(extra, missing))
    return compare_results(expected, {
        "columns": [first.get(col, col) for col in actual["columns"]],
        "rows": [{first.get(k, k): v for k, v in row.items()}
                 for row in actual["rows"]],
    }, **tolerances)
