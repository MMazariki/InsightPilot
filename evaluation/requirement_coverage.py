"""Requirement-coverage checks for structured query plans.

Schema validation (``query_plan_validator``) answers "is this plan well-formed
and does it reference real model objects?".  This module answers a different
question: "does the plan ask for what the requirement asked for?" -- no
missing or extra groupings, the right metrics (by semantics, not by alias),
the right filters and thresholds, ranking direction and result limit.

It is intentionally isolated: it does not compile DAX, call Power BI, or
change validator/compiler behaviour.  Requirements are explicit structured
specs (e.g. ``evaluation/holdout_requirements.json``); deriving them from
free-text questions is a separate, unsolved step and is not attempted here.

Requirement spec format::

    {
      "id": "HOLD-005",
      "supported": true,                  # optional, default true
      "metrics": [{"kind": "measure", "name": "Net Sales"}],
      "group_by": ["DimProduct[Brand]"],
      "column_filters": [
        {"field": "FactSales[Channel]", "operator": "eq", "value": "Delivery"}
      ],
      "metric_filters": [
        {"metric": {"kind": "measure", "name": "Net Sales"},
         "operator": "gt", "value": 100000}
      ],
      "order_by": [
        {"metric": {"kind": "measure", "name": "Net Sales"}, "direction": "desc"}
      ],                                  # or {"field": "Table[Column]", ...}
      "limit": 5,
      "allow_extra_metrics": false        # optional, default false
    }

When ``order_by`` is empty, plan ordering is treated as presentation-only and
is not checked unless the plan also truncates results with a limit.
"""

import math
from collections import Counter


def metric_signature(expression):
    """Return an alias-independent, hashable signature for an expression."""

    kind = expression.get("kind")

    if kind == "measure":
        return ("measure", expression["name"])

    if kind == "distinct_count":
        return ("distinct_count", expression["table"], expression["column"])

    if kind == "divide":
        return (
            "divide",
            metric_signature(expression["numerator"]),
            metric_signature(expression["denominator"]),
        )

    raise ValueError(f"Unsupported expression kind: {kind!r}")


def describe_signature(signature):
    kind = signature[0]

    if kind == "measure":
        return f"[{signature[1]}]"

    if kind == "distinct_count":
        return f"DISTINCTCOUNT({signature[1]}[{signature[2]}])"

    if kind == "divide":
        return (
            f"DIVIDE({describe_signature(signature[1])}, "
            f"{describe_signature(signature[2])})"
        )

    return repr(signature)


def _field(table, column):
    return f"{table}[{column}]"


def _same_value(expected, actual):
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(expected) is type(actual) and expected == actual

    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return math.isclose(expected, actual, rel_tol=1e-9, abs_tol=1e-9)

    return expected == actual


def _finding(code, message, **detail):
    item = {"code": code, "message": message}
    item.update(detail)
    return item


def check_requirement_coverage(plan, requirement):
    """Compare a structured plan with an explicit requirement spec.

    Returns ``{"covered": bool, "findings": [...]}``.  ``plan`` may be
    ``None`` (no plan produced).  The plan is assumed to be schema-shaped but
    is not required to have passed model validation; malformed parts are
    reported as findings rather than raised.
    """

    findings = []
    supported = requirement.get("supported", True)

    if not supported:
        if plan is not None:
            findings.append(_finding(
                "unsupported_intent_answered",
                "Requirement is marked unsupported/ambiguous; the plan "
                "should have been routed to clarification instead.",
            ))
        return {"covered": not findings, "findings": findings}

    if plan is None:
        findings.append(_finding("no_plan", "No query plan was produced."))
        return {"covered": False, "findings": findings}

    # ---------------------------------------------------------------
    # Metrics (compared by semantic signature, never by alias)
    # ---------------------------------------------------------------

    alias_signatures = {}
    plan_metrics = Counter()

    for metric in plan.get("metrics", []):
        try:
            signature = metric_signature(metric["expression"])
        except (KeyError, TypeError, ValueError) as exc:
            findings.append(_finding(
                "unrecognised_metric",
                f"Metric {metric.get('alias')!r} has an unrecognised "
                f"expression: {exc}",
            ))
            continue

        alias_signatures[metric.get("alias")] = signature
        plan_metrics[signature] += 1

    required_metrics = Counter(
        metric_signature(item) for item in requirement.get("metrics", [])
    )

    for signature, count in (required_metrics - plan_metrics).items():
        findings.append(_finding(
            "missing_metric",
            f"Required metric not in plan: {describe_signature(signature)}",
            metric=describe_signature(signature),
            count=count,
        ))

    if not requirement.get("allow_extra_metrics", False):
        for signature, count in (plan_metrics - required_metrics).items():
            findings.append(_finding(
                "extra_metric",
                f"Plan adds a metric not requested: "
                f"{describe_signature(signature)}",
                metric=describe_signature(signature),
                count=count,
            ))

    # ---------------------------------------------------------------
    # Groupings (exact set; extra groupings change result grain)
    # ---------------------------------------------------------------

    plan_groups = [
        _field(item["table"], item["column"])
        for item in plan.get("group_by", [])
    ]
    required_groups = set(requirement.get("group_by", []))

    for field in sorted(required_groups - set(plan_groups)):
        findings.append(_finding(
            "missing_group_by",
            f"Required grouping not in plan: {field}",
            field=field,
        ))

    for field in sorted(set(plan_groups) - required_groups):
        findings.append(_finding(
            "extra_group_by",
            f"Plan groups by a field not requested: {field}",
            field=field,
        ))

    # ---------------------------------------------------------------
    # Column filters
    # ---------------------------------------------------------------

    plan_column_filters = [
        item for item in plan.get("filters", [])
        if item.get("type") == "column"
    ]
    unmatched = list(plan_column_filters)

    for required in requirement.get("column_filters", []):
        match = next(
            (
                item for item in unmatched
                if _field(item["table"], item["column"]) == required["field"]
                and item["operator"] == required["operator"]
                and _same_value(required["value"], item["value"])
            ),
            None,
        )

        if match is not None:
            unmatched.remove(match)
            continue

        same_field = [
            item for item in unmatched
            if _field(item["table"], item["column"]) == required["field"]
        ]

        if same_field:
            item = same_field[0]
            unmatched.remove(item)
            findings.append(_finding(
                "filter_mismatch",
                f"Filter on {required['field']} is "
                f"{item['operator']} {item['value']!r}; expected "
                f"{required['operator']} {required['value']!r}",
                field=required["field"],
            ))
        else:
            findings.append(_finding(
                "missing_filter",
                f"Required filter not in plan: {required['field']} "
                f"{required['operator']} {required['value']!r}",
                field=required["field"],
            ))

    for item in unmatched:
        findings.append(_finding(
            "extra_filter",
            f"Plan adds a filter not requested: "
            f"{_field(item['table'], item['column'])} "
            f"{item['operator']} {item['value']!r}",
            field=_field(item["table"], item["column"]),
        ))

    # ---------------------------------------------------------------
    # Metric (threshold) filters
    # ---------------------------------------------------------------

    plan_metric_filters = []

    for item in plan.get("filters", []):
        if item.get("type") != "metric":
            continue

        signature = alias_signatures.get(item.get("alias"))

        if signature is None:
            findings.append(_finding(
                "unresolved_metric_filter",
                f"Threshold filter references unknown alias "
                f"{item.get('alias')!r}",
            ))
            continue

        plan_metric_filters.append((signature, item))

    remaining = list(plan_metric_filters)

    for required in requirement.get("metric_filters", []):
        signature = metric_signature(required["metric"])
        label = describe_signature(signature)

        match = next(
            (
                pair for pair in remaining
                if pair[0] == signature
                and pair[1]["operator"] == required["operator"]
                and _same_value(required["value"], pair[1]["value"])
            ),
            None,
        )

        if match is not None:
            remaining.remove(match)
            continue

        same_metric = [pair for pair in remaining if pair[0] == signature]

        if same_metric:
            _, item = same_metric[0]
            remaining.remove(same_metric[0])
            findings.append(_finding(
                "threshold_mismatch",
                f"Threshold on {label} is {item['operator']} "
                f"{item['value']!r}; expected {required['operator']} "
                f"{required['value']!r}",
                metric=label,
            ))
        else:
            findings.append(_finding(
                "missing_threshold",
                f"Required threshold not in plan: {label} "
                f"{required['operator']} {required['value']!r}",
                metric=label,
            ))

    for signature, item in remaining:
        findings.append(_finding(
            "extra_threshold",
            f"Plan adds a threshold not requested: "
            f"{describe_signature(signature)} {item['operator']} "
            f"{item['value']!r}",
            metric=describe_signature(signature),
        ))

    # ---------------------------------------------------------------
    # Ordering and limit (ranking semantics)
    # ---------------------------------------------------------------

    def resolve_order_field(name):
        if name in alias_signatures:
            return ("metric", alias_signatures[name])
        return ("field", name)

    plan_order = [
        (resolve_order_field(item["field"]), item["direction"])
        for item in plan.get("order_by", [])
    ]

    required_order = []

    for item in requirement.get("order_by", []):
        if "metric" in item:
            key = ("metric", metric_signature(item["metric"]))
        else:
            key = ("field", item["field"])
        required_order.append((key, item["direction"]))

    def describe_key(key):
        kind, value = key
        return describe_signature(value) if kind == "metric" else value

    # Only the leading required keys are checked; extra trailing plan keys are
    # tie-breakers and do not change the requested ranking.
    for index, (key, direction) in enumerate(required_order):
        if index >= len(plan_order):
            findings.append(_finding(
                "missing_order",
                f"Required ordering not in plan: {describe_key(key)} "
                f"{direction}",
            ))
            continue

        plan_key, plan_direction = plan_order[index]

        if plan_key != key:
            findings.append(_finding(
                "order_field_mismatch",
                f"Ordering position {index + 1} is {describe_key(plan_key)}; "
                f"expected {describe_key(key)}",
            ))
        elif plan_direction != direction:
            findings.append(_finding(
                "order_direction_mismatch",
                f"Ordering on {describe_key(key)} is {plan_direction}; "
                f"expected {direction}",
            ))

    required_limit = requirement.get("limit")
    plan_limit = plan.get("limit")

    if required_limit is None and plan_limit is not None:
        findings.append(_finding(
            "unexpected_limit",
            f"Plan truncates results to {plan_limit} rows; no limit was "
            f"requested.",
        ))
    elif required_limit is not None and plan_limit != required_limit:
        findings.append(_finding(
            "limit_mismatch",
            f"Plan limit is {plan_limit!r}; expected {required_limit}",
        ))

    return {"covered": not findings, "findings": findings}


def assert_requirement_coverage(plan, requirement):
    """Raise ``ValueError`` listing every coverage gap; return plan if covered."""

    result = check_requirement_coverage(plan, requirement)

    if not result["covered"]:
        messages = "; ".join(item["message"] for item in result["findings"])
        raise ValueError(f"Requirement coverage failed: {messages}")

    return plan
