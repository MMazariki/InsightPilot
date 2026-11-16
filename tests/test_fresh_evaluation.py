import json

import pytest
import requests

from evaluation import run_fresh_evaluation as fresh
from evaluation.ollama_query_plan_adapter import build_instructions
from evaluation.ordered_comparator import compare_results_ordered
from evaluation.query_plan_compiler import compile_query_plan
from evaluation.query_plan_validator import validate_query_plan
from evaluation.requirement_coverage import check_requirement_coverage
from tests.test_requirement_coverage import canonical_plan

CASES = fresh.load_cases()
SUPPORTED = [case for case in CASES if case["category"] in fresh.ANSWERABLE]
UNSUPPORTED = [case for case in CASES if case["category"] == "unsupported"]


# ------------------------------------------------------------------
# Case file integrity
# ------------------------------------------------------------------

def test_case_file_shape():
    assert {case["category"] for case in CASES} == {
        "answerable", "empty_result", "unsupported"}
    assert len(SUPPORTED) >= 12
    assert len(UNSUPPORTED) >= 3


def test_fresh_questions_do_not_repeat_holdout():
    holdout = json.loads((fresh.EVAL / "holdout_cases.json")
                         .read_text(encoding="utf-8-sig"))
    old = {case["question"].casefold() for case in holdout}
    assert not old & {case["question"].casefold() for case in CASES}


def test_unsupported_cases_have_no_reference():
    for case in UNSUPPORTED:
        assert case["requirement"] == {"supported": False}
        assert "reference_dax" not in case


@pytest.mark.parametrize("case", SUPPORTED, ids=lambda case: case["id"])
def test_supported_requirement_validates_and_compiles(case):
    assert case["reference_dax"].lstrip().startswith("EVALUATE")
    plan = canonical_plan(case["requirement"])
    validate_query_plan(plan)
    compile_query_plan(plan)
    assert check_requirement_coverage(plan, case["requirement"])["covered"]


# ------------------------------------------------------------------
# Ordered comparator
# ------------------------------------------------------------------

def ranked(*pairs, metric="[Gross Profit]"):
    return {"columns": ["DimStore[Region]", metric],
            "rows": [{"DimStore[Region]": r, metric: v} for r, v in pairs]}


def test_ordered_comparison_passes_same_order():
    expected = ranked(("South", 1.0), ("North", 2.0))
    result = compare_results_ordered(expected, ranked(("South", 1.0), ("North", 2.0)))
    assert result["passed"]
    assert "row order verified" in result["reason"]


def test_ordered_comparison_detects_reversed_order():
    expected = ranked(("South", 1.0), ("North", 2.0))
    result = compare_results_ordered(expected, ranked(("North", 2.0), ("South", 1.0)))
    assert not result["passed"]
    assert result["reason"] == "Row order mismatch"
    assert result["position"] == 0


def test_ordered_comparison_respects_metric_alias():
    expected = ranked(("South", 1.0), ("North", 2.0))
    actual = ranked(("South", 1.0), ("North", 2.0), metric="[GP]")
    assert compare_results_ordered(expected, actual)["passed"]


def test_ordered_comparison_keeps_value_failures():
    expected = ranked(("South", 1.0))
    assert compare_results_ordered(expected, ranked(("South", 9.0)))[
        "reason"] == "Row value mismatch"


# ------------------------------------------------------------------
# Generation: retry only infrastructure failures
# ------------------------------------------------------------------

def http_error(status):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(response=response)


@pytest.mark.parametrize("exc, expected", [
    (requests.ConnectionError(), True),
    (requests.Timeout(), True),
    (http_error(500), True),
    (http_error(400), False),
    (ValueError("bad json"), False),
])
def test_infrastructure_error_classification(exc, expected):
    assert fresh.is_infrastructure_error(exc) is expected


def test_retry_recovers_from_transient_failure():
    calls = []

    def flaky(model, question, instructions):
        calls.append(1)
        if len(calls) == 1:
            raise http_error(500)
        return "{}"

    raw, attempts, errors = fresh.generate_with_retry(
        flaky, "m", "q", "i", sleep=lambda _: None)
    assert (raw, attempts, len(errors)) == ("{}", 2, 1)


def test_retry_is_bounded():
    def down(model, question, instructions):
        raise requests.ConnectionError("refused")

    with pytest.raises(RuntimeError, match="after retries"):
        fresh.generate_with_retry(down, "m", "q", "i", max_attempts=3,
                                  sleep=lambda _: None)


def test_non_infrastructure_errors_are_not_retried():
    calls = []

    def bad_request(model, question, instructions):
        calls.append(1)
        raise http_error(400)

    with pytest.raises(requests.HTTPError):
        fresh.generate_with_retry(bad_request, "m", "q", "i",
                                  sleep=lambda _: None)
    assert len(calls) == 1


def test_plan_record_classifies_invalid_output_without_retry():
    case = {"id": "X"}
    assert fresh.plan_record(case, "m", "not json")["status"] == "invalid_plan"
    unknown = json.dumps({"metrics": [{"alias": "A", "expression": {
        "kind": "measure", "name": "Imaginary"}}], "group_by": [],
        "filters": [], "order_by": [], "limit": None})
    record = fresh.plan_record(case, "m", unknown)
    assert record["status"] == "invalid_plan"
    assert "Unknown measure" in record["error"]


def test_build_instructions_includes_model_and_contract():
    text = build_instructions()
    assert "SEMANTIC MODEL:" in text
    assert "Table FactSales:" in text
    assert "QUERY PLAN JSON SCHEMA:" in text


# ------------------------------------------------------------------
# Scoring
# ------------------------------------------------------------------

def find(case_id):
    return next(case for case in CASES if case["id"] == case_id)


def reference_for(case_id, columns, rows):
    return {"id": case_id, "status": "success", "columns": columns, "rows": rows}


def valid_record(case):
    return {"status": "valid_plan", "plan": canonical_plan(case["requirement"])}


def test_unsupported_answered_fails_and_not_answered_passes():
    case = UNSUPPORTED[0]
    answered = {"status": "valid_plan", "plan": canonical_plan(SUPPORTED[0]["requirement"])}
    assert fresh.evaluate_case(case, answered, None, None)["passed"] is False
    assert fresh.evaluate_case(
        case, {"status": "invalid_plan"}, None, None)["passed"] is True
    infra = fresh.evaluate_case(case, {"status": "infrastructure_error"}, None, None)
    assert infra["passed"] is False
    assert infra["outcome"] == "infrastructure_error"


def test_supported_case_execution_and_strict_pass():
    case = find("FRESH-002")
    rows = [{"DimStore[Region]": "Southern", "[Gross Profit]": 1.0},
            {"DimStore[Region]": "Northern", "[Gross Profit]": 2.0}]
    reference = reference_for(case["id"], ["DimStore[Region]", "[Gross Profit]"], rows)

    def execute(dax):
        assert "TOPN" in dax
        return {"columns": ["DimStore[Region]", "[M0]"], "row_count": 2,
                "rows": [{"DimStore[Region]": r["DimStore[Region]"],
                          "[M0]": r["[Gross Profit]"]} for r in rows]}

    result = fresh.evaluate_case(case, valid_record(case), reference, execute)
    assert result["passed"] and result["strict_passed"]
    assert "row order verified" in result["reason"]


def test_wrong_order_fails_ranked_case():
    case = find("FRESH-002")
    rows = [{"DimStore[Region]": "Southern", "[Gross Profit]": 1.0},
            {"DimStore[Region]": "Northern", "[Gross Profit]": 2.0}]
    reference = reference_for(case["id"], ["DimStore[Region]", "[Gross Profit]"], rows)
    result = fresh.evaluate_case(
        case, valid_record(case), reference,
        lambda dax: {"columns": reference["columns"], "rows": rows[::-1],
                     "row_count": 2})
    assert result["passed"] is False
    assert result["reason"] == "Row order mismatch"


def test_execution_pass_without_coverage_is_not_strict():
    # FRESH-004: dataset only holds 2025, so dropping the year filter returns
    # the same rows. Execution passes; strict scoring must not.
    case = find("FRESH-004")
    plan = canonical_plan(case["requirement"])
    plan["filters"] = []
    rows = [{"FactSales[PaymentMethod]": "Card", "[Net Sales]": 5.0}]
    reference = reference_for(case["id"], ["FactSales[PaymentMethod]", "[Net Sales]"], rows)
    result = fresh.evaluate_case(
        case, {"status": "valid_plan", "plan": plan}, reference,
        lambda dax: {"columns": ["FactSales[PaymentMethod]", "[M0]"],
                     "rows": [{"FactSales[PaymentMethod]": "Card", "[M0]": 5.0}],
                     "row_count": 1})
    assert result["passed"] is True
    assert result["strict_passed"] is False
    assert result["coverage_findings"][0]["code"] == "missing_filter"


def test_empty_result_case_passes_on_empty_execution():
    case = find("FRESH-013")
    reference = reference_for(case["id"], [], [])
    result = fresh.evaluate_case(case, valid_record(case), reference,
                                 lambda dax: {"columns": [], "rows": [], "row_count": 0})
    assert result["passed"] and result["strict_passed"]


def test_execution_error_recorded():
    case = find("FRESH-001")
    reference = reference_for(case["id"], [], [])

    def fail(dax):
        raise RuntimeError("HTTP 400")

    result = fresh.evaluate_case(case, valid_record(case), reference, fail)
    assert result["outcome"] == "execution_error"
    assert result["passed"] is False


def test_missing_reference_raises():
    case = find("FRESH-001")
    with pytest.raises(ValueError, match="No successful reference"):
        fresh.evaluate_case(case, valid_record(case), None, None)


def test_summarise_counts():
    results = [
        {"category": "answerable", "passed": True, "strict_passed": False,
         "plan_status": "valid_plan"},
        {"category": "empty_result", "passed": True, "strict_passed": True,
         "plan_status": "valid_plan"},
        {"category": "unsupported", "passed": False, "strict_passed": False,
         "plan_status": "valid_plan"},
    ]
    summary = fresh.summarise(results)
    assert summary["answerable_execution"]["passed"] == 2
    assert summary["answerable_strict"]["passed"] == 1
    assert summary["unsupported_not_answered"] == {
        "passed": 0, "total": 1, "accuracy": 0.0}
    assert summary["valid_plans"] == 3


@pytest.mark.parametrize("stage, attribute", [
    ("references", "REFERENCES_FILE"),
    ("generate", "PLANS_FILE"),
    ("evaluate", "REPORT_FILE"),
])
def test_stages_refuse_to_overwrite(tmp_path, monkeypatch, stage, attribute):
    existing = tmp_path / "existing.json"
    existing.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(fresh, attribute, existing)
    with pytest.raises(FileExistsError):
        fresh.main([stage])
    assert existing.read_text(encoding="utf-8") == "{}"


def test_cli_rejects_unknown_stage():
    with pytest.raises(SystemExit):
        fresh.main(["everything"])


# ------------------------------------------------------------------
# Second fresh set (fresh_v2)
# ------------------------------------------------------------------

CASES_V2 = fresh.load_cases(fresh.resolve_paths("fresh_v2", "v2")["cases"])
SUPPORTED_V2 = [case for case in CASES_V2 if case["category"] in fresh.ANSWERABLE]


def test_fresh_v2_shape_and_independence():
    assert len(SUPPORTED_V2) == 12
    unsupported = [case for case in CASES_V2 if case["category"] == "unsupported"]
    assert len(unsupported) == 10
    reasons = json.loads((fresh.EVAL / "clarification_schema.json").read_text(
        encoding="utf-8-sig"))["properties"]["clarification"]["properties"][
        "reason"]["enum"]
    for case in unsupported:
        assert case["requirement"] == {"supported": False}
        assert case["expected_reason"] in reasons
        assert "reference_dax" not in case
    earlier = {case["question"].casefold() for case in CASES}
    assert not earlier & {case["question"].casefold() for case in CASES_V2}


@pytest.mark.parametrize("case", SUPPORTED_V2, ids=lambda case: case["id"])
def test_fresh_v2_requirement_validates_and_compiles(case):
    assert case["reference_dax"].lstrip().startswith("EVALUATE")
    plan = canonical_plan(case["requirement"])
    validate_query_plan(plan)
    compile_query_plan(plan)
    assert check_requirement_coverage(plan, case["requirement"])["covered"]


# ------------------------------------------------------------------
# Third fresh set (fresh_v3)
# ------------------------------------------------------------------

CASES_V3 = fresh.load_cases(fresh.resolve_paths("fresh_v3", "v3")["cases"])
SUPPORTED_V3 = [case for case in CASES_V3 if case["category"] in fresh.ANSWERABLE]


def test_fresh_v3_shape_and_independence():
    assert len(SUPPORTED_V3) == 16
    unsupported = [case for case in CASES_V3 if case["category"] == "unsupported"]
    assert len(unsupported) == 6
    for case in unsupported:
        assert case["requirement"] == {"supported": False}
        assert "reference_dax" not in case
    earlier = {case["question"].casefold() for case in CASES + CASES_V2}
    assert not earlier & {case["question"].casefold() for case in CASES_V3}


@pytest.mark.parametrize("case", SUPPORTED_V3, ids=lambda case: case["id"])
def test_fresh_v3_requirement_validates_and_compiles(case):
    assert case["reference_dax"].lstrip().startswith("EVALUATE")
    plan = canonical_plan(case["requirement"])
    validate_query_plan(plan)
    compile_query_plan(plan)
    assert check_requirement_coverage(plan, case["requirement"])["covered"]


# ------------------------------------------------------------------
# Fourth fresh set (fresh_v4)
# ------------------------------------------------------------------

CASES_V4 = fresh.load_cases(fresh.resolve_paths("fresh_v4", "v4")["cases"])
SUPPORTED_V4 = [case for case in CASES_V4 if case["category"] in fresh.ANSWERABLE]


def test_fresh_v4_shape_and_independence():
    assert len(SUPPORTED_V4) == 16
    unsupported = [case for case in CASES_V4 if case["category"] == "unsupported"]
    assert len(unsupported) == 4
    earlier = {case["question"].casefold()
               for case in CASES + CASES_V2 + CASES_V3}
    assert not earlier & {case["question"].casefold() for case in CASES_V4}


@pytest.mark.parametrize("case", SUPPORTED_V4, ids=lambda case: case["id"])
def test_fresh_v4_requirement_validates_compiles_and_passes_semantics(case):
    from evaluation.semantic_checks import check_semantic_rules

    assert case["reference_dax"].lstrip().startswith("EVALUATE")
    plan = canonical_plan(case["requirement"])
    validate_query_plan(plan)
    check_semantic_rules(plan)
    compile_query_plan(plan)
    assert check_requirement_coverage(plan, case["requirement"])["covered"]


# ------------------------------------------------------------------
# Fifth fresh set (fresh_v5)
# ------------------------------------------------------------------

CASES_V5 = fresh.load_cases(fresh.resolve_paths("fresh_v5", "v5")["cases"])
SUPPORTED_V5 = [case for case in CASES_V5 if case["category"] in fresh.ANSWERABLE]


def test_fresh_v5_shape_and_independence():
    assert len(SUPPORTED_V5) == 16
    assert len([c for c in CASES_V5 if c["category"] == "unsupported"]) == 4
    earlier = {case["question"].casefold()
               for case in CASES + CASES_V2 + CASES_V3 + CASES_V4}
    assert not earlier & {case["question"].casefold() for case in CASES_V5}


@pytest.mark.parametrize("case", SUPPORTED_V5, ids=lambda case: case["id"])
def test_fresh_v5_requirement_passes_all_v5_checks(case):
    from evaluation.semantic_checks import check_semantic_rules

    plan = canonical_plan(case["requirement"])
    validate_query_plan(plan)
    check_semantic_rules(plan, reject_single_value_grouping=True)
    compile_query_plan(plan)
    assert check_requirement_coverage(plan, case["requirement"])["covered"]


# ------------------------------------------------------------------
# Sixth fresh set (fresh_v6)
# ------------------------------------------------------------------

CASES_V6 = fresh.load_cases(fresh.resolve_paths("fresh_v6", "v6")["cases"])
SUPPORTED_V6 = [case for case in CASES_V6 if case["category"] in fresh.ANSWERABLE]


def test_fresh_v6_shape_and_independence():
    assert len(SUPPORTED_V6) == 14
    assert len([c for c in CASES_V6 if c["category"] == "unsupported"]) == 6
    earlier = {case["question"].casefold()
               for case in CASES + CASES_V2 + CASES_V3 + CASES_V4 + CASES_V5}
    assert not earlier & {case["question"].casefold() for case in CASES_V6}


@pytest.mark.parametrize("case", SUPPORTED_V6, ids=lambda case: case["id"])
def test_fresh_v6_requirement_passes_all_v6_checks(case):
    from evaluation.semantic_checks import check_semantic_rules

    plan = canonical_plan(case["requirement"])
    validate_query_plan(plan)
    check_semantic_rules(plan, reject_single_value_grouping=True,
                         reference_hints=True)
    compile_query_plan(plan)
    assert check_requirement_coverage(plan, case["requirement"])["covered"]


# ------------------------------------------------------------------
# Seventh fresh set (fresh_v7)
# ------------------------------------------------------------------

CASES_V7 = fresh.load_cases(fresh.resolve_paths("fresh_v7", "v7")["cases"])
SUPPORTED_V7 = [case for case in CASES_V7 if case["category"] in fresh.ANSWERABLE]


def test_fresh_v7_shape_and_independence():
    assert len(SUPPORTED_V7) == 14
    assert len([c for c in CASES_V7 if c["category"] == "unsupported"]) == 6
    earlier = {case["question"].casefold() for case in
               CASES + CASES_V2 + CASES_V3 + CASES_V4 + CASES_V5 + CASES_V6}
    earlier.add("what were total units sold by product category?")
    assert not earlier & {case["question"].casefold() for case in CASES_V7}


@pytest.mark.parametrize("case", SUPPORTED_V7, ids=lambda case: case["id"])
def test_fresh_v7_requirement_passes_all_v7_checks(case):
    from evaluation.semantic_checks import check_semantic_rules

    plan = canonical_plan(case["requirement"])
    validate_query_plan(plan)
    check_semantic_rules(plan, reject_single_value_grouping=True,
                         reference_hints=True, expression_kind_hints=True)
    compile_query_plan(plan)
    assert check_requirement_coverage(plan, case["requirement"])["covered"]
