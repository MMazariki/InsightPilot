import json
from datetime import datetime, timezone
from pathlib import Path

from src.powerbi_query_service import (
    PowerBIQueryService,
    PowerBIQueryError,
)

ROOT = Path(__file__).resolve().parents[1]

QUERIES_FILE = ROOT / "evaluation" / "reference_queries.json"
RESULTS_DIR = ROOT / "evaluation" / "results"
RESULTS_FILE = RESULTS_DIR / "reference_results.json"


def main():
    queries = json.loads(
        QUERIES_FILE.read_text(encoding="utf-8-sig")
    )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    if RESULTS_FILE.exists():
        raise FileExistsError(
            f"Results already exist: {RESULTS_FILE}"
        )

    service = PowerBIQueryService()

    results = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_id": service.dataset_id,
        "cases": [],
    }

    try:
        for item in queries:
            case_id = item["id"]
            dax = item["reference_dax"]

            print(f"Executing {case_id}...")

            try:
                response = service.execute_dax(dax)

                results["cases"].append({
                    "id": case_id,
                    "reference_dax": dax,
                    "status": "success",
                    "columns": response["columns"],
                    "rows": response["rows"],
                    "row_count": response["row_count"],
                })

                print(f"  Success: {response['row_count']} rows")

            except (PowerBIQueryError, ValueError) as exc:
                results["cases"].append({
                    "id": case_id,
                    "reference_dax": dax,
                    "status": "error",
                    "error": str(exc),
                })

                print(f"  Failed: {exc}")

    finally:
        service.close()

    RESULTS_FILE.write_text(
        json.dumps(results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"\nResults saved to: {RESULTS_FILE}")


if __name__ == "__main__":
    main()
