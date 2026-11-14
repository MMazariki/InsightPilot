import pytest

from evaluation.result_comparator import compare_results


def make_result(columns, rows):
    return {"columns": columns, "rows": rows}


def test_identical_results():
    expected = make_result(
        ["[Net Sales]"],
        [{"[Net Sales]": 1000.0}],
    )
    assert compare_results(expected, expected)["passed"] is True


def test_different_row_order():
    expected = make_result(
        ["Category", "Sales"],
        [
            {"Category": "A", "Sales": 100},
            {"Category": "B", "Sales": 200},
        ],
    )
    actual = make_result(
        ["Category", "Sales"],
        [
            {"Category": "B", "Sales": 200},
            {"Category": "A", "Sales": 100},
        ],
    )
    assert compare_results(expected, actual)["passed"] is True


def test_floating_point_tolerance():
    expected = make_result(
        ["Sales"], [{"Sales": 1000.00}]
    )
    actual = make_result(
        ["Sales"], [{"Sales": 1000.005}]
    )
    assert compare_results(expected, actual)["passed"] is True


def test_incorrect_numerical_result():
    expected = make_result(
        ["Sales"], [{"Sales": 1000}]
    )
    actual = make_result(
        ["Sales"], [{"Sales": 1200}]
    )
    assert compare_results(expected, actual)["passed"] is False


def test_column_mismatch():
    expected = make_result(
        ["Net Sales"], [{"Net Sales": 1000}]
    )
    actual = make_result(
        ["Gross Sales"], [{"Gross Sales": 1000}]
    )
    result = compare_results(expected, actual)

    assert result["passed"] is False
    assert result["reason"] == "Column mismatch"


def test_missing_rows():
    expected = make_result(
        ["Region"], [{"Region": "North"}, {"Region": "South"}]
    )
    actual = make_result(
        ["Region"], [{"Region": "North"}]
    )
    result = compare_results(expected, actual)

    assert result["passed"] is False
    assert result["reason"] == "Row count mismatch"
