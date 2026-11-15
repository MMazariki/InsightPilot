import json
import re
from pathlib import Path

from src.powerbi_query_service import PowerBIQueryService
from evaluation.scoring import score_evaluation

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evaluation" / "results"


def load_json(filename):
    return json.loads(
        (RESULTS / filename).read_text(encoding="utf-8-sig")
    )


def main():
    references = load_json("reference_results.json")
    generations = load_json("ollama_generations.json")

    output_file = RESULTS / "ollama_evaluation.json"

    if output_file.exists():
        raise FileExistsError(
            f"Evaluation already exists: {output_file}"
        )

    service = PowerBIQueryService()

    if service.dataset_id != references["dataset_id"]:
        service.close()
        raise ValueError("Reference dataset ID does not match.")

    models = sorted(set(item["model"] for item in generations))
    executions = {model: [] for model in models}

    try:
        for item in generations:
            model = item["model"]
            case_id = item["id"]
            dax = item.get("candidate_dax", "")

            print(f"\n{model} | {case_id}", flush=True)

            record = {"id": case_id, "status": "error"}

            if item.get("status") != "generated":
                record["error"] = "Generation failed"

            elif not re.match(r"^\s*EVALUATE\b", dax, re.I):
                record["error"] = "Missing initial EVALUATE"

            else:
                try:
                    result = service.execute_dax(dax)

                    record = {
                        "id": case_id,
                        "status": "success",
                        "columns": result["columns"],
                        "rows": result["rows"],
                        "row_count": result["row_count"],
                    }

                except Exception as exc:
                    record["error"] = str(exc)

            executions[model].append(record)

            print(record["status"], flush=True)

            if record["status"] == "error":
                print(record.get("error", ""), flush=True)

    finally:
        service.close()

    scores = {}

    for model in models:
        scores[model] = score_evaluation(
            references["cases"],
            executions[model],
        )

        print(f"\nMODEL: {model}")
        print(json.dumps(scores[model], indent=2))

    output_file.write_text(
        json.dumps(
            {
                "dataset_id": references["dataset_id"],
                "scores": scores,
                "executions": executions,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(f"\nEvaluation saved to: {output_file}")


if __name__ == "__main__":
    main()
