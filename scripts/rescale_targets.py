"""Set FactTarget.csv targets to a realistic level, from actual sales.

The targets delivered with the dataset are about 38 times actual net
sales (2025: 33.5M SAR of targets against 0.86M of net sales), so target
achievement reads about 2.6%. This script sets every monthly target per
store and category to that month's actual net sales for the store and
category, plus 5-15% (a stretch target):

* months with complete sales: actual x U(1.05, 1.15)
* the current, unfinished month and later months of the year: the same
  month a year earlier x the store and category's growth so far this year
  x U(1.05, 1.15)
* a store and category with no sales in a month: its average month that
  year x U(1.05, 1.15)

Rows, keys and order are unchanged; only SalesTargetSAR changes.

    python scripts/rescale_targets.py           # dry run: summary only
    python scripts/rescale_targets.py --write   # back up, then rewrite

``--write`` copies the folder to ``<folder>_backup_before_targets`` first.
Refresh and publish the dataset in Power BI Desktop afterwards.
"""

import argparse
import datetime as dt
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_DIR = Path(r"L:\Downloads\Saudi_Retail_MVP_PowerBI_Dataset")
UPLIFT = (1.05, 1.15)


def monthly_actuals(folder):
    sales = pd.read_csv(folder / "FactSales.csv",
                        usecols=["DateKey", "StoreKey", "ProductKey", "NetSalesSAR"])
    products = pd.read_csv(folder / "DimProduct.csv", usecols=["ProductKey", "Category"])
    sales = sales.merge(products, on="ProductKey", how="left")
    sales["YearMonth"] = (sales.DateKey // 10000).astype(str) + "-" + (
        (sales.DateKey // 100) % 100).map("{:02d}".format)
    last_day = dt.datetime.strptime(str(sales.DateKey.max()), "%Y%m%d").date()
    actual = sales.groupby(["YearMonth", "StoreKey", "Category"]).NetSalesSAR.sum()
    return actual, last_day


def new_targets(targets, actual, last_day, rng):
    current = f"{last_day.year}-{last_day.month:02d}"
    month_end = (last_day.replace(day=28) + dt.timedelta(days=4)).replace(day=1) \
        - dt.timedelta(days=1)
    complete_through = current if last_day == month_end else None

    def is_complete(year_month):
        if complete_through:
            return year_month <= complete_through
        return year_month < current

    # Growth so far this year per store and category (complete months only).
    year = last_day.year
    growth = {}
    for (store, category), _ in targets.groupby(["StoreKey", "Category"]):
        months = [f"{year}-{m:02d}" for m in range(1, 13)
                  if is_complete(f"{year}-{m:02d}")]
        now = sum(actual.get((m, store, category), 0.0) for m in months)
        before = sum(actual.get((f"{year - 1}{m[4:]}", store, category), 0.0)
                     for m in months)
        growth[(store, category)] = now / before if before else 1.0

    values, sources = [], []
    for row in targets.itertuples(index=False):
        key = (row.YearMonth, row.StoreKey, row.Category)
        uplift = rng.uniform(*UPLIFT)
        if is_complete(row.YearMonth) and actual.get(key, 0.0) > 0:
            base, source = actual[key], "actual"
        elif not is_complete(row.YearMonth):
            last_year = f"{int(row.YearMonth[:4]) - 1}{row.YearMonth[4:]}"
            base = actual.get((last_year, row.StoreKey, row.Category), 0.0) \
                * growth[(row.StoreKey, row.Category)]
            source = "projected"
        else:
            same_year = [v for (m, s, c), v in actual.items()
                         if m[:4] == row.YearMonth[:4] and s == row.StoreKey
                         and c == row.Category]
            base, source = (np.mean(same_year) if same_year else 0.0), "average"
        values.append(round(base * uplift, 2))
        sources.append(source)
    return values, sources


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    folder = args.data_dir
    path = folder / "FactTarget.csv"
    targets = pd.read_csv(path, dtype={"YearMonth": str})
    actual, last_day = monthly_actuals(folder)
    values, sources = new_targets(targets, actual.to_dict(), last_day,
                                  np.random.default_rng(args.seed))

    out = targets.copy()
    out["SalesTargetSAR"] = values
    assert len(out) == len(targets) and (out.SalesTargetSAR > 0).all()

    yearly = pd.DataFrame({
        "Year": targets.YearMonth.str[:4],
        "OldTarget": targets.SalesTargetSAR, "NewTarget": out.SalesTargetSAR,
    }).groupby("Year").sum()
    net = actual.groupby(actual.index.get_level_values(0).str[:4]).sum()
    yearly["NetSales"] = net
    yearly["Achievement %"] = (yearly.NetSales / yearly.NewTarget * 100).round(1)
    print(f"Sales data ends {last_day}.")
    print(yearly.round(0).to_string())
    print("Target sources:", pd.Series(sources).value_counts().to_dict())

    if not args.write:
        print("\nDry run: nothing was written. Add --write to apply.")
        return
    backup = folder.with_name(folder.name + "_backup_before_targets")
    if backup.exists():
        sys.exit(f"Backup folder already exists: {backup}. Nothing was changed.")
    shutil.copytree(folder, backup)
    out.to_csv(path, index=False, lineterminator="\n")
    print(f"\nBackup: {backup}\nRewrote {path} ({len(out):,} rows).")


if __name__ == "__main__":
    main()
