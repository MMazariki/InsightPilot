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
    # added 2026-11-19 (APP2-027 asked to change a target value)
    "increase", "decrease", "raise", "reduce", "boost", "adjust", "correct",
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
    # So is a verb used as a noun: "change %", "change in sales", "drop
    # from 2024", "change and growth" (added 2026-11-17).
    + r"(?!\s*%)"
    + r"(?!\s+(?:percent|percentage|in|of|from|between|over|vs|versus|rate|"
    + r"and|or|per)\b)"
    + r"(?:\W+[\w'&-]+){0,5}?\W+"
    + r"(?P<object>" + "|".join(DATA_OBJECTS) + r")\b",
    re.IGNORECASE,
)


# Questions that need data from outside the company's sales model: the
# market, competitors, industry figures (APP3-019: "market share" was
# answered as a share of the company's own net sales). Added 2026-11-19.
OUTSIDE_DATA = re.compile(
    r"\bmarket\s+(?:share|size|sizes|growth|position|leaders?|value|potential|"
    r"penetration|trends?)\b|\bmarket\b(?=.*\b(?:compare|versus|vs)\b)|"
    r"\bcompetitors?\b|\bcompetition\b|\brivals?\b|"
    r"\bindustry\s+(?:average|averages|benchmarks?|standards?|norms?|growth|"
    r"figures?|data|margin)\b|\bbenchmarks?\b|"
    r"\b(?:population|gdp|inflation|weather|footfall)\b",
    re.IGNORECASE)


def screen_outside_data(question):
    """A clarification dict for questions that need outside data, else ``None``."""

    if not isinstance(question, str):
        raise TypeError("Question must be a string")
    match = OUTSIDE_DATA.search(question)
    if match is None:
        return None
    return {
        "clarification": {
            "reason": "out_of_scope",
            "message": (
                f"Answering this needs data from outside the company's sales model "
                f"(\"{match.group(0).strip()}\": market, competitor or industry "
                "figures). InsightPilot only has the company's own sales, targets, "
                "products, stores and customers."
            ),
        }
    }


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
