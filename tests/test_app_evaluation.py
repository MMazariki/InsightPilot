import json
from pathlib import Path

from evaluation.run_app_evaluation import compare, normalise, row_matches

ROOT = Path(__file__).resolve().parents[1]


def test_values_match_ignoring_column_names_and_extra_columns():
    reference = {"N": 100.004, "S": "Delivery"}
    answer = {"Channel": "Delivery", "[Net Sales]": 100.0, "Share %": "25.0%"}
    assert row_matches(reference, answer)
    assert not row_matches({"N": 101.0}, answer)


def test_percent_text_matches_a_fraction():
    assert row_matches({"P": 0.27214}, {"Change %": "272.1%"}) is False
    assert row_matches({"P": 2.72114}, {"Change %": "272.1%"})
    assert normalise("1,234.5%")[1] == 12.345


def test_blank_cells():
    assert row_matches({"Y": 2021, "C": None}, {"Year": 2021, "Change": None,
                                                "Change %": ""})


def test_rows_and_top1_modes():
    reference = [{"a": "x", "n": 1.0}, {"a": "y", "n": 2.0}]
    assert compare(reference, list(reversed(reference)), "rows")[0]
    assert not compare(reference, reference[:1], "rows")[0]
    assert compare(reference, [reference[0]], "top1")[0]
    assert not compare(reference, [reference[1]], "top1")[0]


def test_case_set_is_well_formed():
    data = json.loads((ROOT / "evaluation" / "app_cases_v1.json").read_text(encoding="utf-8"))
    ids = [c["id"] for c in data["cases"]]
    assert len(ids) == len(set(ids)) == 33
    for case in data["cases"]:
        if case["category"] == "answerable":
            assert case["mode"] in ("rows", "top1") and case["reference_dax"]
