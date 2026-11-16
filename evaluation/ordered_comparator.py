"""Order-sensitive result comparison.

``compare_results`` / ``compare_results_with_metric_alias`` are deliberately
row-order-insensitive, so they cannot tell whether a requested ranking or
sort order was respected. This module adds a positional check for cases whose
requirement specifies ordering.
"""

import math

from evaluation.alias_aware_comparator import compare_results_with_metric_alias


def _equivalent(a, b, relative_tolerance, absolute_tolerance):
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b

    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(
            a, b, rel_tol=relative_tolerance, abs_tol=absolute_tolerance
        )

    return a == b


def compare_results_ordered(expected, actual, relative_tolerance=1e-6,
                            absolute_tolerance=0.01,
                            base_comparator=compare_results_with_metric_alias):
    """Alias-aware comparison that also requires identical row order.

    ``base_comparator`` defaults to the single-alias comparator used for the
    fresh_v1 and fresh_v2 reports.
    """

    tolerances = {
        "relative_tolerance": relative_tolerance,
        "absolute_tolerance": absolute_tolerance,
    }
    result = base_comparator(expected, actual, **tolerances)

    if not result["passed"]:
        return result

    mapping = result.get("alias_mapping", {})
    columns = list(expected["columns"])

    for index, (expected_row, actual_row) in enumerate(
        zip(expected["rows"], actual["rows"])
    ):
        renamed = {mapping.get(key, key): value
                   for key, value in actual_row.items()}

        if not all(
            _equivalent(expected_row.get(column), renamed.get(column),
                        relative_tolerance, absolute_tolerance)
            for column in columns
        ):
            return {
                "passed": False,
                "reason": "Row order mismatch",
                "position": index,
                "expected_row": expected_row,
                "actual_row": actual_row,
            }

    result["reason"] += " (row order verified)"
    return result
