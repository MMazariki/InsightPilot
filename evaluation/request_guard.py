"""Deterministic pre-planner screen for write/destructive requests.

This is a ROUTING heuristic, not a security boundary: the execution path is
already read-only (Power BI ExecuteQueries, compiled from a validated plan).
Its purpose is to stop requests such as "delete all 2025 sales" from being
silently re-interpreted by the planner as an unrelated read query, and to
route them to a clarification instead -- without calling the model.

A request is flagged when a write verb starts a clause (sentence start, after
a politeness/intent lead-in, or after "and"/"then") and is followed within a
few words by a data-object noun. Bare analytical uses ("a drop in sales",
"add up net sales", "the change in revenue") are not flagged.
"""

import re

WRITE_VERBS = (
    "delete", "remove", "drop", "truncate", "erase", "wipe", "purge",
    "update", "insert", "add", "create", "modify", "change", "edit",
    "overwrite", "rename", "set", "alter", "replace", "reset",
)

DATA_OBJECTS = (
    r"records?", r"rows?", r"transactions?", r"tables?", r"entries",
    r"entry", r"targets?", r"measures?", r"columns?", r"categor(?:y|ies)",
    r"products?", r"stores?", r"customers?", r"data(?:set)?", r"orders?",
    r"brands?", r"models?", r"reports?", r"sales", r"figures?",
    r"values?", r"history",
)

CLAUSE_START = (
    r"(?:^|[.;:!?,]\s*|\b(?:and|then|please|to|you|must|should|"
    r"can|could|would|will|let's|lets)\s+)"
)

WRITE_PATTERN = re.compile(
    CLAUSE_START
    + r"(?P<verb>" + "|".join(WRITE_VERBS) + r")\b"
    # "add up" / "sum up" style aggregation phrasing is analytical.
    + r"(?!\s+up\b)"
    + r"(?:\W+[\w'&-]+){0,5}?\W+"
    + r"(?P<object>" + "|".join(DATA_OBJECTS) + r")\b",
    re.IGNORECASE,
)


def screen_request(question):
    """Return a clarification dict for write-style requests, else ``None``."""

    if not isinstance(question, str):
        raise TypeError("Question must be a string")

    match = WRITE_PATTERN.search(question.strip())

    if match is None:
        return None

    return {
        "clarification": {
            "reason": "write_request",
            "message": (
                "This assistant can only read and analyse data; it cannot "
                f"{match.group('verb').lower()} {match.group('object').lower()}. "
                "Please rephrase as an analytical question or make the change "
                "in the source system."
            ),
        }
    }
