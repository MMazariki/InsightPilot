from evaluation.alias_aware_comparator import compare_results_with_metric_alias


def dataset(metric="[Total Discounts]", values=(100.0, 200.0)):
    return {
        "columns": ["FactSales[Channel]", metric],
        "rows": [
            {"FactSales[Channel]": channel, metric: value}
            for channel, value in zip(
                ("Delivery", "In-Store"), values
            )
        ],
    }


def test_identical_results_pass():
    expected = dataset()
    assert compare_results_with_metric_alias(expected, dataset())["passed"]


def test_different_metric_alias_passes():
    result = compare_results_with_metric_alias(
        dataset(),
        dataset(metric="[Discounts]"),
    )
    assert result["passed"]
    assert result["reason"] == "Results equivalent (metric alias differs)"


def test_different_metric_values_fail():
    result = compare_results_with_metric_alias(
        dataset(),
        dataset(metric="[Discounts]", values=(100.0, 250.0)),
    )
    assert not result["passed"]


def test_different_row_count_fails():
    actual = dataset(metric="[Discounts]")
    actual["rows"].pop()
    assert not compare_results_with_metric_alias(dataset(), actual)["passed"]


def test_different_grouping_column_fails():
    actual = dataset(metric="[Discounts]")
    actual["columns"][0] = "DimStore[Region]"
    actual["rows"] = [
        {"DimStore[Region]": row["FactSales[Channel]"],
         "[Discounts]": row["[Discounts]"]}
        for row in actual["rows"]
    ]
    assert not compare_results_with_metric_alias(dataset(), actual)["passed"]


def test_different_grouping_values_fail():
    actual = dataset(metric="[Discounts]")
    actual["rows"][0]["FactSales[Channel]"] = "Online"
    assert not compare_results_with_metric_alias(dataset(), actual)["passed"]


def test_single_metric_alias_passes():
    expected = {
        "columns": ["[Net Sales]"],
        "rows": [{"[Net Sales]": 863462.46}],
    }
    actual = {
        "columns": ["[Total Net Sales]"],
        "rows": [{"[Total Net Sales]": 863462.46}],
    }
    assert compare_results_with_metric_alias(expected, actual)["passed"]
