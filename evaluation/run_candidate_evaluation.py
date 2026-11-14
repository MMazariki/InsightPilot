import json
from pathlib import Path

from src.powerbi_query_service import PowerBIQueryService
from evaluation.scoring import score_evaluation

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "evaluation"
RESULTS = EVAL / "results"


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main():
    references = load_json(RESULTS / "reference_results.json")
    candidates = load_json(EVAL / "candidate_queries.json")

    RESULTS.mkdir(parents=True, exist_ok=True)

    output_file = RESULTS / "candidate_evaluation.json"

    if output_file.exists():
        raise FileExistsError(
            f"Evaluation already exists: {output_file}"
        )

    service = PowerBIQueryService()
    executions = []

    try:
        for candidate in candidates:
            case_id = candidate["id"]

            print(f"Executing {case_id}...")

            try:
                result = service.execute_dax(
                    candidate["candidate_dax"]
                )

                executions.append({
                    "id": case_id,
                    "status": "success",
                    "columns": result["columns"],
                    "rows": result["rows"],
                    "row_count": result["row_count"],
                })

                print("  Success")

            except Exception as exc:
                executions.append({
                    "id": case_id,
                    "status": "error",
                    "error": str(exc),
                })

                print(f"  Failed: {exc}")

    finally:
        service.close()

    score = score_evaluation(
        references["cases"],
        executions,
    )

    report = {
        "evaluation_type": "manual_candidate_baseline",
        "reference_dataset_id": references["dataset_id"],
        "candidate_dataset_id": service.dataset_id,
        "score": score,
    }

    output_file.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("\nEVALUATION RESULTS")
    print(json.dumps(score, indent=2))
    print(f"\nSaved to: {output_file}")


if __name__ == "__main__":
    main()
