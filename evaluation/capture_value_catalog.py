"""Capture allowed values for low-cardinality text columns (read-only).

Run from the project root:
    python -m evaluation.capture_value_catalog

Reads the committed schema snapshot, queries VALUES() for each Text column
that is not an identifier (ID/Key/Code/SKU) and keeps columns with at most
MAX_VALUES distinct values. Writes evaluation/semantic_value_catalog.json and
refuses to overwrite it. The catalog contains attribute values only (e.g.
channel, region and month names), no measures, rows or identifiers.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from evaluation.query_plan_validator import load_model

ROOT = Path(__file__).resolve().parents[1]
CATALOG_FILE = ROOT / "evaluation" / "semantic_value_catalog.json"
MAX_VALUES = 25
IDENTIFIER = re.compile(r"(ID|Key|Code|SKU)$")


def candidate_columns(model):
    for table in model["Tables"]:
        for column in table.get("Columns", []):
            if column["Type"] == "Text" and not IDENTIFIER.search(column["Name"]):
                yield table["Name"], column["Name"]


def main():
    if CATALOG_FILE.exists():
        raise FileExistsError(f"Catalog will not be overwritten: {CATALOG_FILE}")

    from src.powerbi_query_service import PowerBIQueryService

    service = PowerBIQueryService()
    columns = {}
    skipped = {}
    try:
        for table, column in candidate_columns(load_model()):
            result = service.execute_dax(f"EVALUATE VALUES('{table}'[{column}])")
            values = sorted(
                str(next(iter(row.values())))
                for row in result["rows"]
                if next(iter(row.values())) is not None
            )
            key = f"{table}[{column}]"
            if len(values) <= MAX_VALUES:
                columns[key] = values
                print(f"{key}: {len(values)} values")
            else:
                skipped[key] = len(values)
                print(f"{key}: skipped ({len(values)} values)")
    finally:
        service.close()

    CATALOG_FILE.write_text(json.dumps({
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "max_values": MAX_VALUES,
        "columns": columns,
        "skipped_high_cardinality": skipped,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nSaved: {CATALOG_FILE}")


if __name__ == "__main__":
    main()
