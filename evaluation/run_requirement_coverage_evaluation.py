"""Offline requirement-coverage evaluation of saved structured query plans.

Run from the project root:
    python -m evaluation.run_requirement_coverage_evaluation

Reads only local artifacts. It does NOT call Power BI, Ollama, Azure AI
Foundry, or any network service. If the executed evaluation report
(query_plan_evaluation_v1.json) exists, it also measures agreement between the
coverage gate and the execution-based verdicts; it never re-scores execution.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from evaluation.query_plan_validator import validate_query_plan
from evaluation.requirement_coverage import check_requirement_coverage

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "evaluation"
RESULTS = EVAL / "results"
REQUIREMENTS_FILE = EVAL / "holdout_requirements.json"
GENERATIONS_FILE = RESULTS / "ollama_query_plans_v1_merged.json"
EXECUTION_FILE = RESULTS / "query_plan_evaluation_v1.json"
OUTPUT_FILE = RESULTS / "requirement_coverage_evaluation_v1.json"


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def evaluate_records(generations, requirements):
    """Return per-record gate results: schema validity AND coverage."""

    by_id = {item["id"]: item for item in requirements}
    records = []

    for item in generations:
        requirement = by_id.get(item["id"])
        if requirement is None:
            raise ValueError(f"No requirement spec for case {item['id']}")

        plan = item.get("plan")
        schema_error = None

        if plan is None:
            schema_error = item.get("error", "No plan produced")
        else:
            try:
                validate_query_plan(plan)
            except ValueError as exc:
                schema_error = str(exc)

        try:
            coverage = check_requirement_coverage(plan, requirement)
        except (KeyError, TypeError, ValueError) as exc:
            coverage = {
                "covered": False,
                "findings": [{
                    "code": "coverage_check_error",
                    "message": str(exc),
                }],
            }

        records.append({
            "model": item["model"],
            "id": item["id"],
            "schema_valid": schema_error is None,
            "schema_error": schema_error,
            "covered": coverage["covered"],
            "findings": coverage["findings"],
            "gate_pass": schema_error is None and coverage["covered"],
        })

    return records


def agreement_with_execution(records, execution_report):
    """Compare gate verdicts with execution-scored verdicts per model."""

    summary = {}

    for model, score in execution_report["scores"].items():
        executed = {case["id"]: case["passed"] for case in score["cases"]}
        matrix = {"both_pass": [], "both_fail": [],
                  "gate_pass_exec_fail": [], "gate_fail_exec_pass": []}

        for record in records:
            if record["model"] != model or record["id"] not in executed:
                continue

            gate = record["gate_pass"]
            passed = executed[record["id"]]

            if gate and passed:
                matrix["both_pass"].append(record["id"])
            elif not gate and not passed:
                matrix["both_fail"].append(record["id"])
            elif gate:
                matrix["gate_pass_exec_fail"].append(record["id"])
            else:
                matrix["gate_fail_exec_pass"].append(record["id"])

        total = sum(len(ids) for ids in matrix.values())
        agree = len(matrix["both_pass"]) + len(matrix["both_fail"])
        summary[model] = {
            "total": total,
            "agreement": agree,
            "agreement_rate": agree / total if total else None,
            **matrix,
        }

    return summary


def main():
    if OUTPUT_FILE.exists():
        raise FileExistsError(
            f"Existing report will not be overwritten: {OUTPUT_FILE}"
        )

    requirements = load_json(REQUIREMENTS_FILE)["cases"]
    generations = load_json(GENERATIONS_FILE)
    records = evaluate_records(generations, requirements)

    models = sorted({record["model"] for record in records})
    gate_scores = {}

    for model in models:
        rows = [record for record in records if record["model"] == model]
        passed = sum(record["gate_pass"] for record in rows)
        gate_scores[model] = {
            "total": len(rows),
            "schema_valid": sum(record["schema_valid"] for record in rows),
            "covered": sum(record["covered"] for record in rows),
            "gate_pass": passed,
        }
        print(f"\n{model}: gate {passed}/{len(rows)} "
              f"(schema-valid {gate_scores[model]['schema_valid']}, "
              f"covered {gate_scores[model]['covered']})")

        for record in rows:
            status = "PASS" if record["gate_pass"] else "FAIL"
            print(f"  {record['id']}: {status}")
            if record["schema_error"]:
                print(f"    schema: {record['schema_error']}")
            for finding in record["findings"]:
                print(f"    {finding['code']}: {finding['message']}")

    agreement = None
    if EXECUTION_FILE.exists():
        agreement = agreement_with_execution(
            records, load_json(EXECUTION_FILE)
        )
        for model, item in agreement.items():
            print(f"\n{model}: gate/execution agreement "
                  f"{item['agreement']}/{item['total']}")
            print(f"  gate pass, execution fail: "
                  f"{item['gate_pass_exec_fail']}")
            print(f"  gate fail, execution pass: "
                  f"{item['gate_fail_exec_pass']}")
    else:
        print(f"\nNo execution report at {EXECUTION_FILE.name}; "
              f"agreement not computed.")

    report = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_generations": GENERATIONS_FILE.name,
        "source_requirements": REQUIREMENTS_FILE.name,
        "source_execution_report": (
            EXECUTION_FILE.name if EXECUTION_FILE.exists() else None
        ),
        "note": "Offline; no Power BI or model calls. Gate = schema/model "
                "validation AND requirement coverage.",
        "gate_scores": gate_scores,
        "agreement_with_execution": agreement,
        "records": records,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nReport saved: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
