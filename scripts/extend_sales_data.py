"""Append 2026 data to the synthetic Saudi retail source CSVs.

The published Power BI dataset is imported from a folder of CSV files that
cover 1 Jan - 31 Dec 2025. This script appends, without touching any 2025
row:

* FactSales.csv   sales lines for 1 Jan 2026 up to ``--until`` (default: today)
* DimDate.csv     every date of 2026
* FactTarget.csv  monthly targets for all of 2026
* README.csv      period and row count

The 2025 data is flat (every day and month look alike). The 2026 data is
generated with patterns so charts have something to show:

* growth through the year, with different growth per store
* Ramadan (18 Feb - 19 Mar 2026) and Eid al-Fitr (20 - 23 Mar) uplift
* busier Thursdays to Saturdays
* summer uplift for beverages, September uplift for accessories
* delivery and click-and-collect gaining share, cash declining
* deeper discounts during Ramadan, Eid and month-ends
* real orders: every line of an order shares date, store, customer,
  channel and payment method

Prices, costs and the row formulas are taken from the existing data, and no
new store, product, customer or category value is introduced.

Usage (dry run prints a summary and writes nothing):

    python scripts/extend_sales_data.py
    python scripts/extend_sales_data.py --write

``--write`` first copies the folder to ``<folder>_backup_2025_only`` and
refuses to run if 2026 rows are already present. The output is deterministic
for a given ``--until`` date and ``--seed``.

After writing, the dataset must be refreshed in Power BI (see README).
"""

import argparse
import datetime as dt
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_DIR = Path(r"L:\Downloads\Saudi_Retail_MVP_PowerBI_Dataset")
YEAR = 2026
RAMADAN = (dt.date(2026, 2, 18), dt.date(2026, 3, 19))
EID = (dt.date(2026, 3, 20), dt.date(2026, 3, 23))

WEEKDAY_FACTOR = {  # Monday = 0
    0: 0.90, 1: 0.90, 2: 0.96, 3: 1.10, 4: 1.26, 5: 1.20, 6: 0.97,
}
MONTH_FACTOR = {
    1: 0.96, 2: 1.00, 3: 1.04, 4: 0.97, 5: 1.00, 6: 1.06, 7: 1.10,
    8: 1.07, 9: 1.03, 10: 1.00, 11: 1.04, 12: 1.08,
}
LINES_PER_ORDER = ([1, 2, 3, 4, 5, 6], [0.44, 0.30, 0.15, 0.07, 0.03, 0.01])
DISCOUNT_RATES = [0.0, 0.03, 0.05, 0.10, 0.15]
DISCOUNT_NORMAL = [0.475, 0.12, 0.20, 0.155, 0.05]
DISCOUNT_PROMO = [0.30, 0.10, 0.22, 0.24, 0.14]


def in_range(day, span):
    return span[0] <= day <= span[1]


def day_factor(day, year_progress):
    factor = (1.10 + 0.22 * year_progress)          # growth over 2025
    factor *= MONTH_FACTOR[day.month] * WEEKDAY_FACTOR[day.weekday()]
    if in_range(day, RAMADAN):
        factor *= 1.15
    if in_range(day, EID):
        factor *= 1.35
    return factor


def category_factor(category, day):
    if category == "Beverages" and day.month in (6, 7, 8):
        return 1.30
    if category == "Food" and in_range(day, RAMADAN):
        return 1.40
    if category == "Snacks" and in_range(day, EID):
        return 1.35
    if category == "Accessories" and day.month == 9:
        return 1.45
    return 1.0


def is_promo(day):
    next_month = (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    month_end = (next_month - day).days <= 3
    return in_range(day, RAMADAN) or in_range(day, EID) or month_end


def channel_weights(progress):
    delivery = 0.25 + 0.10 * progress
    collect = 0.07 + 0.04 * progress
    return ["In-Store", "Delivery", "Click & Collect"], [
        1 - delivery - collect, delivery, collect]


def payment_weights(channel, progress):
    names = ["Card", "Cash", "Online", "Wallet"]
    if channel == "In-Store":
        cash = 0.30 - 0.09 * progress
        wallet = 0.12 + 0.08 * progress
        return names, [1 - cash - wallet, cash, 0.0, wallet]
    return names, [0.33, 0.04, 0.43, 0.20]


def build_dates():
    rows = []
    day = dt.date(YEAR, 1, 1)
    while day.year == YEAR:
        rows.append({
            "DateKey": int(day.strftime("%Y%m%d")),
            "Date": day.isoformat(),
            "Year": day.year,
            "Quarter": f"Q{(day.month - 1) // 3 + 1}",
            "MonthNumber": day.month,
            "MonthName": day.strftime("%B"),
            "YearMonth": day.strftime("%Y-%m"),
            "WeekNumber": day.isocalendar()[1],
            "DayName": day.strftime("%A"),
            "IsWeekend": day.weekday() in (4, 5, 6),
        })
        day += dt.timedelta(days=1)
    return pd.DataFrame(rows)


def build_targets(targets, rng):
    base = targets[targets.YearMonth.str.startswith("2025-")].copy()
    store_growth = {
        key: rng.uniform(1.02, 1.14) for key in sorted(base.StoreKey.unique())}
    base["YearMonth"] = base.YearMonth.str.replace("2025-", f"{YEAR}-", regex=False)
    base["SalesTargetSAR"] = [
        round(value * store_growth[store] * rng.uniform(0.98, 1.02), 2)
        for value, store in zip(base.SalesTargetSAR, base.StoreKey)]
    return base


def build_sales(sales, products, stores, customers, until, rng):
    days = [dt.date(YEAR, 1, 1) + dt.timedelta(days=i)
            for i in range((until - dt.date(YEAR, 1, 1)).days + 1)]
    base_lines_per_day = len(sales) / 365
    mean_lines = float(np.dot(*LINES_PER_ORDER))

    # Store popularity from 2025, with a different 2026 growth per store.
    store_share = sales.StoreKey.value_counts(normalize=True).sort_index()
    store_keys = store_share.index.to_numpy()
    store_growth = rng.uniform(0.80, 1.40, size=len(store_keys))
    store_ramp = rng.uniform(-0.15, 0.30, size=len(store_keys))
    store_city = stores.set_index("StoreKey").City.to_dict()

    # Product popularity from 2025; inactive products sell far less.
    product_share = sales.ProductKey.value_counts(normalize=True)
    products = products.set_index("ProductKey")
    product_keys = products.index.to_numpy()
    product_base = np.array([
        product_share.get(key, 0.002) * (1.0 if products.IsActive[key] else 0.15)
        for key in product_keys])
    product_category = products.Category.to_numpy()

    # A minority of loyal customers buy much more often.
    customer_keys = customers.CustomerKey.to_numpy()
    loyalty = rng.lognormal(mean=0.0, sigma=0.9, size=len(customer_keys))
    by_city = {
        city: np.flatnonzero(customers.City.to_numpy() == city)
        for city in customers.City.unique()}
    all_customer_weights = loyalty / loyalty.sum()

    quantity_values = sales.Quantity.value_counts(normalize=True).sort_index()

    line_id = int(sales.SalesLineID.max())
    order_number = int(sales.OrderID.str[4:].astype(int).max())
    rows = []

    for index, day in enumerate(days):
        progress = index / 364
        expected_orders = base_lines_per_day * day_factor(day, progress) / mean_lines
        expected_orders *= rng.lognormal(0.0, 0.07)
        order_count = rng.poisson(expected_orders)

        store_weights = store_share.to_numpy() * store_growth * (1 + store_ramp * progress)
        store_weights = store_weights / store_weights.sum()
        product_weights = product_base * np.array(
            [category_factor(category, day) for category in product_category])
        product_weights = product_weights / product_weights.sum()
        discounts = DISCOUNT_PROMO if is_promo(day) else DISCOUNT_NORMAL
        channels, channel_p = channel_weights(progress)

        for _ in range(order_count):
            order_number += 1
            store = int(rng.choice(store_keys, p=store_weights))
            local = by_city.get(store_city[store])
            if local is not None and len(local) and rng.random() < 0.72:
                weights = loyalty[local] / loyalty[local].sum()
                customer = int(customer_keys[rng.choice(local, p=weights)])
            else:
                customer = int(rng.choice(customer_keys, p=all_customer_weights))
            channel = str(rng.choice(channels, p=channel_p))
            payments, payment_p = payment_weights(channel, progress)
            payment = str(rng.choice(payments, p=payment_p))
            line_count = int(rng.choice(LINES_PER_ORDER[0], p=LINES_PER_ORDER[1]))
            chosen = rng.choice(product_keys, size=line_count, replace=False,
                                p=product_weights)
            for product in chosen:
                line_id += 1
                quantity = int(rng.choice(quantity_values.index.to_numpy(),
                                          p=quantity_values.to_numpy()))
                price = float(products.UnitPriceSAR[product])
                unit_cost = float(products.UnitCostSAR[product])
                gross = round(quantity * price, 2)
                discount = round(gross * float(rng.choice(DISCOUNT_RATES, p=discounts)), 2)
                net = round(gross - discount, 2)
                cost = round(quantity * unit_cost, 2)
                rows.append({
                    "SalesLineID": line_id,
                    "OrderID": f"ORD-{order_number:07d}",
                    "DateKey": int(day.strftime("%Y%m%d")),
                    "StoreKey": store,
                    "ProductKey": int(product),
                    "CustomerKey": customer,
                    "Quantity": quantity,
                    "UnitPriceSAR": price,
                    "GrossSalesSAR": gross,
                    "DiscountSAR": discount,
                    "NetSalesSAR": net,
                    "CostSAR": cost,
                    "GrossProfitSAR": round(net - cost, 2),
                    "Channel": channel,
                    "PaymentMethod": payment,
                })
    return pd.DataFrame(rows, columns=list(sales.columns))


def append_csv(path, frame):
    """Append rows in the file's own format (LF line endings, no index)."""

    text = frame.to_csv(index=False, header=False, lineterminator="\n")
    existing = path.read_bytes()
    with path.open("ab") as handle:
        if existing and not existing.endswith(b"\n"):
            handle.write(b"\n")
        handle.write(text.encode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--until", type=dt.date.fromisoformat,
                        default=dt.date.today(),
                        help="last sales date to generate (YYYY-MM-DD)")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--write", action="store_true",
                        help="back up the folder and append the new rows")
    args = parser.parse_args()

    folder = args.data_dir
    if not dt.date(YEAR, 1, 1) <= args.until <= dt.date(YEAR, 12, 31):
        sys.exit(f"--until must be a date in {YEAR}.")

    sales = pd.read_csv(folder / "FactSales.csv")
    dates = pd.read_csv(folder / "DimDate.csv")
    targets = pd.read_csv(folder / "FactTarget.csv")
    products = pd.read_csv(folder / "DimProduct.csv")
    stores = pd.read_csv(folder / "DimStore.csv")
    customers = pd.read_csv(folder / "DimCustomer.csv")

    if (sales.DateKey >= YEAR * 10000).any() or (dates.Year >= YEAR).any() \
            or targets.YearMonth.str.startswith(f"{YEAR}-").any():
        sys.exit(f"{folder} already contains {YEAR} rows; nothing was changed.")

    rng = np.random.default_rng(args.seed)
    new_dates = build_dates()
    new_targets = build_targets(targets, rng)
    new_sales = build_sales(sales, products, stores, customers, args.until, rng)

    # Integrity checks before anything is written.
    assert new_sales.SalesLineID.is_unique
    assert not set(new_sales.SalesLineID) & set(sales.SalesLineID)
    assert not set(new_sales.OrderID) & set(sales.OrderID)
    assert set(new_sales.DateKey) <= set(new_dates.DateKey)
    assert set(new_sales.StoreKey) <= set(stores.StoreKey)
    assert set(new_sales.ProductKey) <= set(products.ProductKey)
    assert set(new_sales.CustomerKey) <= set(customers.CustomerKey)
    assert set(new_sales.Channel) <= set(sales.Channel)
    assert set(new_sales.PaymentMethod) <= set(sales.PaymentMethod)
    assert np.allclose(new_sales.NetSalesSAR,
                       new_sales.GrossSalesSAR - new_sales.DiscountSAR)
    assert np.allclose(new_sales.GrossProfitSAR,
                       new_sales.NetSalesSAR - new_sales.CostSAR, atol=0.011)
    per_order = new_sales.groupby("OrderID")[
        ["DateKey", "StoreKey", "CustomerKey", "Channel", "PaymentMethod"]].nunique()
    assert (per_order == 1).all().all()
    assert len(new_targets) == 12 * 80 and len(new_dates) == 365

    monthly = new_sales.assign(Month=new_sales.DateKey // 100).groupby("Month").agg(
        Lines=("SalesLineID", "size"), Orders=("OrderID", "nunique"),
        NetSales=("NetSalesSAR", "sum")).round(0)
    print(f"Folder: {folder}")
    print(f"New sales lines: {len(new_sales):,} in {new_sales.OrderID.nunique():,} "
          f"orders, {new_sales.DateKey.min()} to {new_sales.DateKey.max()}")
    print(f"2025 for comparison: {len(sales):,} lines, net sales "
          f"{sales.NetSalesSAR.sum():,.0f}; 2026 so far: net sales "
          f"{new_sales.NetSalesSAR.sum():,.0f}")
    print(monthly.to_string())
    print(f"New dates: {len(new_dates)}; new target rows: {len(new_targets)}")

    if not args.write:
        print("\nDry run: nothing was written. Add --write to apply.")
        return

    backup = folder.with_name(folder.name + "_backup_2025_only")
    if backup.exists():
        sys.exit(f"Backup folder already exists: {backup}. Nothing was changed.")
    shutil.copytree(folder, backup)
    print(f"\nBackup of the original files: {backup}")

    append_csv(folder / "FactSales.csv", new_sales)
    append_csv(folder / "DimDate.csv", new_dates)
    append_csv(folder / "FactTarget.csv", new_targets)

    readme = folder / "README.csv"
    text = readme.read_text(encoding="utf-8")
    total = len(sales) + len(new_sales)
    text = text.replace(
        "Period,1 Jan 2025 to 31 Dec 2025",
        f"Period,1 Jan 2025 to {args.until.day} {args.until.strftime('%b %Y')} "
        f"(2026 appended by InsightPilot scripts/extend_sales_data.py)")
    text = text.replace(
        '"One row per sales line; 24,000 rows"',
        f'"One row per sales line; {total:,} rows"')
    readme.write_text(text, encoding="utf-8", newline="")
    print(f"Appended. FactSales.csv now has {total:,} rows.")


if __name__ == "__main__":
    main()
