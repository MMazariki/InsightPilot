import json
from pathlib import Path

CASES_FILE = (
    Path(__file__).resolve().parents[1]
    / "evaluation"
    / "cases.json"
)


def load_cases():
    return json.loads(CASES_FILE.read_text(encoding="utf-8-sig"))


def test_evaluation_dataset_not_empty():
    cases = load_cases()
    assert isinstance(cases, list)
    assert len(cases) > 0


def test_evaluation_case_structure():
    for case in load_cases():
        assert isinstance(case["id"], str)
        assert case["id"].strip()

        assert isinstance(case["question"], str)
        assert case["question"].strip()

        assert isinstance(case["expected_measures"], list)
        assert case["expected_measures"]
        assert all(
            isinstance(m, str) and m.strip()
            for m in case["expected_measures"]
        )

        assert isinstance(case["expected_dimensions"], list)
        assert all(
            isinstance(d, str) and d.strip()
            for d in case["expected_dimensions"]
        )


def test_evaluation_ids_unique():
    ids = [case["id"] for case in load_cases()]
    assert len(ids) == len(set(ids))
