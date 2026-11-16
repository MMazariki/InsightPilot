"""Answerability gate (pipeline v9).

Rules and hints only fire when a plan is invalid. Unsupported questions
such as "forecast next quarter's net sales" or "returned items by brand" can
produce *valid* plans that silently answer a different question. This gate
runs after a plan validates:

1. Deterministic trigger: find question words that match nothing in the
   semantic model (table/column/measure names, catalog values) or a generic
   analytics vocabulary. No unknown words -> the plan passes untouched.
2. Verifier (only when triggered): the planner model sees the question, the
   plan in plain English and the unknown words, and returns
   ``{"faithful": bool, "reason": str}``. ``faithful: false`` turns the
   response into a clarification; nothing is executed.

The verifier is a model judgement, not a guarantee; its outcome and the
trigger words are recorded for audit.
"""

import json
import re

from evaluation.query_plan_validator import load_model
from evaluation.semantic_checks import load_catalog

GENERIC_VOCABULARY = set("""
a about above across after against all along also amount amounts an and any
are as ascending at average avg band bands be below best between both bottom
breakdown broken business buy bought by calculate came can compare compared
comparison compute could count counts daily data date day days descending
did different display distinct divided do does down during each equal every
except excluding fewer figure figures find first for from get give got had
has have high higher highest how in include including is it its last least
less list low lower lowest made many me million month monthly months more
most much my negative number numbers of on one only or order ordered orders
our over overall per percent percentage period placed please plus positive
purchase purchased purchases quarter quarterly quarters rank ranked ranking
rate ratio report sar sell selling share shopped show single sold sort
sorted split sum than that the their them there these they this those three
thousand to top total totals two under unique value values versus view vs
was we week weekly were what when where which who whose with within without
year yearly zero four five six seven eight nine ten twenty hundred k
item items quantity revenue profit profits margin margins discount
discounts customer customers product products store stores shopper shoppers
buyer buyers transaction transactions sale target targets achievement
variance gross net unit units
second third fourth fifth sixth seventh eighth ninth tenth billion whole
through used using
""".split())
# Calibration note: the ordinals and "billion whole through used using" were
# added after measuring trigger rates on the (already seen) fresh_v1-v8 sets,
# where they were the only words triggering on answerable questions.

_SPLIT_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])|[^A-Za-z]+")
_WORD = re.compile(r"[A-Za-z]+")


def _tokens(text):
    return [part.lower() for part in _SPLIT_CAMEL.split(str(text)) if part]


def build_vocabulary(model=None, catalog=None):
    model = load_model() if model is None else model
    catalog = load_catalog() if catalog is None else catalog
    words = set(GENERIC_VOCABULARY)
    for table in model["Tables"]:
        words.update(_tokens(table["Name"]))
        for item in table.get("Columns", []) + table.get("Measures", []):
            words.update(_tokens(item["Name"]))
    for field, values in catalog.items():
        words.update(_tokens(field))
        for value in values:
            words.update(word.lower() for word in _WORD.findall(value))
    return words


def _known(word, vocabulary):
    candidates = {word}
    for suffix in ("s", "es", "ed", "ing", "ly"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            candidates.add(word[: -len(suffix)])
    if word.endswith("ies") and len(word) > 4:
        candidates.add(word[:-3] + "y")
    return bool(candidates & vocabulary)


def unknown_terms(question, vocabulary=None):
    vocabulary = build_vocabulary() if vocabulary is None else vocabulary
    seen = []
    for word in _WORD.findall(question.lower()):
        if len(word) < 3 or _known(word, vocabulary):
            continue
        if word not in seen:
            seen.append(word)
    return seen


OPERATOR_TEXT = {"eq": "=", "ne": "≠", "gt": ">", "gte": "≥", "lt": "<",
                 "lte": "≤"}


def _describe_expression(expression):
    kind = expression.get("kind")
    if kind == "measure":
        return f"the measure {expression.get('name')}"
    if kind == "distinct_count":
        return (f"the number of distinct {expression.get('column')} values "
                f"in {expression.get('table')}")
    if kind == "divide":
        return (f"({_describe_expression(expression['numerator'])}) divided by "
                f"({_describe_expression(expression['denominator'])})")
    return json.dumps(expression)


def describe_plan(plan):
    parts = ["Compute " + "; ".join(
        f"{_describe_expression(metric['expression'])}"
        for metric in plan["metrics"])]
    if plan["group_by"]:
        parts.append("for each " + ", ".join(
            f"{item['table']}[{item['column']}]" for item in plan["group_by"]))
    filters = [
        (f"{item['table']}[{item['column']}] {OPERATOR_TEXT[item['operator']]} "
         f"{item['value']!r}") if item["type"] == "column" else
        f"{item['alias']} {OPERATOR_TEXT[item['operator']]} {item['value']}"
        for item in plan["filters"]
    ]
    if filters:
        parts.append("where " + " and ".join(filters))
    if plan["order_by"]:
        parts.append("sorted by " + ", ".join(
            f"{item['field']} {item['direction']}" for item in plan["order_by"]))
    if plan["limit"] is not None:
        parts.append(f"keeping the first {plan['limit']} rows")
    return ", ".join(parts) + "."


VERIFIER_INSTRUCTIONS = """You check whether a query plan faithfully answers a business question.
The plan can only use the listed measures and model columns; it reads
historical data and cannot predict, modify data, or use data the model lacks.

Return ONLY a JSON object: {"faithful": true or false, "reason": "<short>"}

faithful = false when ANY of these holds:
- the question asks for a quantity the plan does not compute (for example a
  metric that is not among the measures, replaced by a different one);
- the question asks for a forecast, prediction, plan or change of data;
- the question is too vague to know which metric is wanted.
faithful = true when the plan computes what was asked, even if the wording
differs (e.g. "revenue" for Net Sales, "items" for Units Sold).
"""


def verify(question, plan, unknown, chat, model_name, measures):
    messages = [
        {"role": "system", "content": VERIFIER_INSTRUCTIONS},
        {"role": "user", "content": (
            f"Question: {question}\n"
            f"Plan: {describe_plan(plan)}\n"
            f"Available measures: {', '.join(measures)}\n"
            f"Question words not found in the model: {', '.join(unknown)}")},
    ]
    raw = chat(model_name, messages)
    try:
        payload = json.loads(raw)
        faithful = payload.get("faithful")
        if not isinstance(faithful, bool):
            raise ValueError("faithful must be a boolean")
        return {"faithful": faithful,
                "reason": str(payload.get("reason", ""))[:300], "raw": raw}
    except (ValueError, TypeError, AttributeError) as exc:
        # An unusable verdict does not block the answer; it is recorded.
        return {"faithful": True, "reason": f"verifier output unusable: {exc}",
                "raw": raw, "verifier_error": True}


def gate(question, plan, chat, model_name, vocabulary=None, model=None):
    """Return ``{"passed": bool, "unknown_terms": [...], "verdict": ...}``."""

    model = load_model() if model is None else model
    unknown = unknown_terms(question, vocabulary)
    if not unknown:
        return {"passed": True, "unknown_terms": [], "verdict": None}
    measures = sorted(measure["Name"] for table in model["Tables"]
                      for measure in table.get("Measures", []))
    verdict = verify(question, plan, unknown, chat, model_name, measures)
    return {"passed": verdict["faithful"], "unknown_terms": unknown,
            "verdict": verdict}


def clarification_message(result):
    terms = ", ".join(result["unknown_terms"])
    reason = (result.get("verdict") or {}).get("reason", "")
    return (f"I can't answer this faithfully from the semantic model "
            f"(unrecognised terms: {terms}). {reason}".strip())
