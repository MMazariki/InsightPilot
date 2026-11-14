import pytest

from evaluation.scoring import score_evaluation


def make_case(case_id, value=None, status="success"):
    case = {"id": case_id, "status": status}
    if status == "success":
        case["columns"] = ["[Net Sales]"]
        case["rows"] = [{"[Net Sales]": value}]
    return case


def test_all_candidates_correct():
    references = [
        make_case("EVAL-001", 100),
        make_case("EVAL-002", 200),
    ]
    candidates = [
        make_case("EVAL-001", 100),
        make_case("EVAL-002", 200),
    ]

    result = score_evaluation(references, candidates)

    assert result["total"] == 2
    assert result["passed"] == 2
    assert result["failed"] == 0
    assert result["accuracy"] == 1.0


def test_incorrect_candidate():
    references = [make_case("EVAL-001", 100)]
    candidates = [make_case("EVAL-001", 150)]

    result = score_evaluation(references, candidates)

    assert result["passed"] == 0
    assert result["failed"] == 1
    assert result["accuracy"] == 0.0


def test_missing_candidate():
    references = [make_case("EVAL-001", 100)]

    result = score_evaluation(references, [])

    assert result["cases"][0]["status"] == "missing_candidate"
    assert result["failed"] == 1


def test_failed_candidate():
    references = [make_case("EVAL-001", 100)]
    candidates = [make_case("EVAL-001", status="error")]

    result = score_evaluation(references, candidates)

    assert result["cases"][0]["status"] == "candidate_failed"
    assert result["accuracy"] == 0.0


def test_unavailable_reference():
    references = [make_case("EVAL-001", status="error")]

    result = score_evaluation(references, [])

    assert result["total"] == 0
    assert result["accuracy"] is None


def test_duplicate_candidate_ids():
    references = [make_case("EVAL-001", 100)]
    candidates = [
        make_case("EVAL-001", 100),
        make_case("EVAL-001", 100),
    ]

    with pytest.raises(ValueError, match="Duplicate candidate"):
        score_evaluation(references, candidates)
