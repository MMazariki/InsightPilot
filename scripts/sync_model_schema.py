"""Bring the app's copy of the data model up to date with the published one.

The planner, the answer checks and the query validator read the model's
tables, columns and measures from ``evaluation/semantic_model_schema.json``,
a snapshot taken on 2026-11-16. Measures or columns added in Power BI
Desktop reach the app only once they are in that file. This script reads
the published dataset (read-only DAX ``INFO.VIEW`` queries, using the
app's saved sign-in) and lists the differences:

    python scripts/sync_model_schema.py            # report only
    python scripts/sync_model_schema.py --write    # apply
    python scripts/sync_model_schema.py --write --skip "Orders" --skip "Unit Price"

Hidden columns and measures and Power BI's automatic date tables are
ignored. ``--skip`` keeps a measure out of the app (for example while its
formula is being fixed). Measure formulas cannot be read through the API;
check what a new measure computes before relying on it.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SCHEMA_FILE = ROOT / "evaluation" / "semantic_model_schema.json"
TYPE_NAMES = {"Number": "Double", "Integer": "Integer", "Text": "Text",
              "Date": "DateTime", "DateTime": "DateTime",
              "Boolean": "Boolean", "True/False": "Boolean",
              "Decimal": "Double", "Currency": "Double"}


def _is_auto(table):
    return table.startswith(("LocalDateTable_", "DateTableTemplate_"))


def read_live(service):
    columns = service.execute_dax(
        'EVALUATE SELECTCOLUMNS(INFO.VIEW.COLUMNS(), "T", [Table], '
        '"N", [Name], "Type", [DataType], "Hidden", [IsHidden])')["rows"]
    measures = service.execute_dax(
        'EVALUATE SELECTCOLUMNS(INFO.VIEW.MEASURES(), "T", [Table], '
        '"N", [Name], "Type", [DataType], "Hidden", [IsHidden])')["rows"]

    def keep(rows):
        return [(r["[T]"], r["[N]"], TYPE_NAMES.get(r["[Type]"], r["[Type]"]))
                for r in rows if not r["[Hidden]"] and not _is_auto(r["[T]"])]

    return keep(columns), keep(measures)


def compare(schema, live_columns, live_measures, skip=()):
    tables = {t["Name"]: t for t in schema["Tables"]}
    have_cols = {(t, c["Name"]) for t, tab in tables.items()
                 for c in tab.get("Columns", [])}
    have_meas = {(t, m["Name"]) for t, tab in tables.items()
                 for m in tab.get("Measures", [])}
    live_cols = {(t, n) for t, n, _ in live_columns if t in tables}
    live_meas = {(t, n) for t, n, _ in live_measures if t in tables}
    return {
        "new_columns": [c for c in live_columns if (c[0], c[1]) not in have_cols and c[0] in tables],
        "gone_columns": sorted(have_cols - live_cols),
        "new_measures": [m for m in live_measures
                         if (m[0], m[1]) not in have_meas and m[0] in tables
                         and m[1] not in skip],
        "gone_measures": sorted(have_meas - live_meas),
        "skipped": [m for m in live_measures if m[1] in skip],
    }


def apply(schema, diff):
    tables = {t["Name"]: t for t in schema["Tables"]}
    for table, name in diff["gone_columns"]:
        tables[table]["Columns"] = [c for c in tables[table]["Columns"]
                                    if c["Name"] != name]
    for table, name in diff["gone_measures"]:
        tables[table]["Measures"] = [m for m in tables[table]["Measures"]
                                     if m["Name"] != name]
    for table, name, kind in diff["new_columns"]:
        tables[table].setdefault("Columns", []).append({"Name": name, "Type": kind})
    for table, name, kind in diff["new_measures"]:
        tables[table].setdefault("Measures", []).append({"Name": name, "Type": kind})
    return schema


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--skip", action="append", default=[],
                        help="measure name to keep out of the app")
    args = parser.parse_args()

    from src.powerbi_query_service import PowerBIQueryService

    service = PowerBIQueryService()
    try:
        live_columns, live_measures = read_live(service)
    finally:
        service.close()

    raw = SCHEMA_FILE.read_text(encoding="utf-8-sig")
    schema = json.loads(raw)
    diff = compare(schema, live_columns, live_measures, set(args.skip))
    for key, items in diff.items():
        print(f"{key}: {items if items else '-'}")
    if not any(diff[k] for k in ("new_columns", "gone_columns",
                                  "new_measures", "gone_measures")):
        print("The app's model is up to date.")
        return
    if args.write:
        bom = "﻿" if raw.startswith("﻿") else ""
        SCHEMA_FILE.write_text(
            bom + json.dumps(apply(schema, diff), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        print(f"Updated {SCHEMA_FILE.relative_to(ROOT)}. Restart the app.")
    else:
        print("Report only; add --write to apply.")


if __name__ == "__main__":
    main()
