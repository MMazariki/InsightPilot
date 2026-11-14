import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "evaluation"


def load_json(filename):
    return json.loads(
        (EVAL_DIR / filename).read_text(encoding="utf-8-sig")
    )


def test_reference_queries_match_cases():
    cases = load_json("cases.json")
    references = load_json("reference_queries.json")

    case_ids = [case["id"] for case in cases]
    reference_ids = [ref["id"] for ref in references]

    assert len(reference_ids) == len(set(reference_ids))
    assert set(case_ids) == set(reference_ids)


def test_reference_queries_contain_evaluate():
    references = load_json("reference_queries.json")

    for ref in references:
        dax = ref["reference_dax"]

        assert isinstance(dax, str)
        assert re.search(r"\bEVALUATE\b", dax, re.IGNORECASE)


def test_reference_queries_use_expected_fields():
    cases = {
        case["id"]: case
        for case in load_json("cases.json")
    }

    for ref in load_json("reference_queries.json"):
        case = cases[ref["id"]]
        dax = ref["reference_dax"]

        for measure in case["expected_measures"]:
            assert f"[{measure}]" in dax, ref["id"]

        normalized_dax = re.sub(
            r"\s+", "", dax.replace("'", "")
        )

        for dimension in case["expected_dimensions"]:
            normalized_dimension = re.sub(
                r"\s+", "", dimension
            )
            assert normalized_dimension in normalized_dax, ref["id"]
