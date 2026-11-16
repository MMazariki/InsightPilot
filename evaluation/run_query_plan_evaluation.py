"""Evaluate Ollama-generated structured plans against captured Power BI references.

Run from the project root:
    python -m evaluation.run_query_plan_evaluation

Uses existing local JSON artifacts and the Power BI ExecuteQueries API. It does
not invoke Ollama, Azure AI Foundry, or any paid model API.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from evaluation.alias_aware_comparator import compare_results_with_metric_alias
from evaluation.query_plan_compiler import compile_query_plan
from evaluation.scoring import score_evaluation
from src.powerbi_query_service import PowerBIQueryService

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evaluation" / "results"
REFERENCE_FILE = RESULTS / "holdout_reference_results.json"
GENERATIONS_FILE = RESULTS / "ollama_query_plans_v1_merged.json"
OUTPUT_FILE = RESULTS / "query_plan_evaluation_v1.json"
MODELS = ("qwen2.5-coder:3b", "qwen2.5-coder:7b")


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def verify_inputs(references, generations):
    reference_cases = references["cases"]
    reference_ids = [case["id"] for case in reference_cases]

    if len(reference_ids) != 12 or len(set(reference_ids)) != 12:
        raise ValueError("Expected exactly 12 unique holdout reference cases")

    failed_references = [
        case["id"] for case in reference_cases
        if case.get("status") != "success"
    ]
    if failed_references:
        raise ValueError(f"Unsuccessful reference cases: {failed_references}")

    if len(generations) != 24:
        raise ValueError(f"Expected 24 generated records; got {len(generations)}")

    for model in MODELS:
        records = [item for item in generations if item["model"] == model]
        record_ids = [item["id"] for item in records]
        if len(record_ids) != 12 or set(record_ids) != set(reference_ids):
            raise ValueError(f"Incomplete or mismatched cases for {model}")
        if len(record_ids) != len(set(record_ids)):
            raise ValueError(f"Duplicate case IDs for {model}")

    unknown_models = {item["model"] for item in generations} - set(MODELS)
    if unknown_models:
        raise ValueError(f"Unexpected models: {sorted(unknown_models)}")


def main():
    if OUTPUT_FILE.exists():
        raise FileExistsError(f"Existing report will not be overwritten: {OUTPUT_FILE}")

    references = load_json(REFERENCE_FILE)
    generations = load_json(GENERATIONS_FILE)
    verify_inputs(references, generations)

    executions = {model: [] for model in MODELS}
    compiled_count = 0

    service = PowerBIQueryService()
    try:
        if service.dataset_id != references["dataset_id"]:
            raise ValueError("Live dataset ID differs from reference dataset ID")

        for item in generations:
            model = item["model"]
            case_id = item["id"]
            record = {"id": case_id, "status": "error"}
            print(f"\n{model} | {case_id}", flush=True)

            if item.get("status") != "valid_plan":
                record["error"] = item.get("error", "Plan not validated")
                record["failure_type"] = "invalid_plan"
            else:
                try:
                    # Recompile and revalidate the JSON plan. Never trust a
                    # generated DAX string stored in the model output file.
                    dax = compile_query_plan(item["plan"])
                    compiled_count += 1
                    result = service.execute_dax(dax)
                    record = {
                        "id": case_id,
                        "status": "success",
                        "candidate_dax": dax,
                        "columns": result["columns"],
                        "rows": result["rows"],
                        "row_count": result["row_count"],
                    }
                except Exception as exc:
                    record["error"] = str(exc)
                    record["failure_type"] = "execution_or_compilation_error"

            executions[model].append(record)
            print(f"  {record['status']}", flush=True)
            if record["status"] != "success":
                print(f"  {record.get('error', '')}", flush=True)
    finally:
        service.close()

    scores = {}
    for model in MODELS:
        score = score_evaluation(
            references["cases"],
            executions[model],
            comparator=compare_results_with_metric_alias,
        )
        scores[model] = score
        print(f"\n{model}: {score['passed']}/{score['total']} "
              f"({score['accuracy']:.1%})")
        for item in score["cases"]:
            status = "PASS" if item["passed"] else "FAIL"
            print(f"  {item['id']}: {status} - "
                  f"{item.get('reason', item['status'])}")

    report = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_id": references["dataset_id"],
        "source_generations": GENERATIONS_FILE.name,
        "source_references": REFERENCE_FILE.name,
        "comparison_mode": "metric_alias_aware",
        "compiled_valid_plans": compiled_count,
        "scores": scores,
        "executions": executions,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"\nReport saved: {OUTPUT_FILE}")
    print("Review false positives/negatives manually, including sorting and extra metrics.")


if __name__ == "__main__":
    main()
