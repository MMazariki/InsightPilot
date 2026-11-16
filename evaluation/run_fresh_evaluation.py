"""Fresh held-out evaluation of the structured planning pipeline.

Run from the project root, one stage at a time:

    python -m evaluation.run_fresh_evaluation references   # Power BI (read-only)
    python -m evaluation.run_fresh_evaluation generate     # local Ollama only
    python -m evaluation.run_fresh_evaluation evaluate     # Power BI (read-only)

Cases, requirement specs and reference DAX live in evaluation/fresh_cases_v1.json
and were committed before any generation. No stage calls Azure AI Foundry or a
paid API, and no stage overwrites an existing result file.

Scoring per model:
  * answerable (incl. empty_result): execution pass = valid plan, compiled
    from JSON, executed, results equivalent to reference (row order also
    checked when the requirement specifies ordering). Strict pass =
    execution pass AND requirement coverage.
  * unsupported: pass = no valid plan produced. The v1 prompt has no
    refusal/clarification channel, so a "pass" here means the plan failed
    validation, not that the model deliberately declined.
"""

import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from evaluation.alias_aware_comparator import compare_results_with_metric_alias
from evaluation.ordered_comparator import compare_results_ordered
from evaluation.query_plan_compiler import compile_query_plan
from evaluation.query_plan_validator import validate_query_plan
from evaluation.requirement_coverage import check_requirement_coverage

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "evaluation"
RESULTS = EVAL / "results"
CASES_FILE = EVAL / "fresh_cases_v1.json"
REFERENCES_FILE = RESULTS / "fresh_v1_reference_results.json"
PLANS_FILE = RESULTS / "fresh_v1_query_plans.json"
REPORT_FILE = RESULTS / "fresh_v1_evaluation.json"

MODELS = ("qwen2.5-coder:3b", "qwen2.5-coder:7b")
NUM_CTX = 8192          # same as the v1 holdout generation, for comparability
MAX_ATTEMPTS = 3        # infrastructure failures only
RETRY_DELAY_SECONDS = 10
ANSWERABLE = ("answerable", "empty_result")


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_cases():
    cases = load_json(CASES_FILE)["cases"]
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate fresh case IDs")
    return cases


def refuse_overwrite(path):
    if path.exists():
        raise FileExistsError(f"Existing result will not be overwritten: {path}")


def write_json(path, payload):
    RESULTS.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"\nSaved: {path}")


# ----------------------------------------------------------------------
# Stage 1: reference capture
# ----------------------------------------------------------------------

def capture_references():
    from src.powerbi_query_service import PowerBIQueryService

    refuse_overwrite(REFERENCES_FILE)
    cases = [case for case in load_cases() if case["category"] in ANSWERABLE]
    service = PowerBIQueryService()
    output = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_id": service.dataset_id,
        "cases": [],
    }

    try:
        for case in cases:
            print(f"Reference {case['id']}...", flush=True)
            record = {"id": case["id"], "reference_dax": case["reference_dax"]}
            try:
                result = service.execute_dax(case["reference_dax"])
                record.update(status="success", columns=result["columns"],
                              rows=result["rows"],
                              row_count=result["row_count"])
                print(f"  {result['row_count']} rows", flush=True)
            except Exception as exc:
                record.update(status="error", error=str(exc))
                print(f"  ERROR {exc}", flush=True)
            output["cases"].append(record)
    finally:
        service.close()

    write_json(REFERENCES_FILE, output)


# ----------------------------------------------------------------------
# Stage 2: plan generation (local Ollama)
# ----------------------------------------------------------------------

def is_infrastructure_error(exc):
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(exc, requests.HTTPError):
        response = exc.response
        return response is None or response.status_code >= 500
    return False


def generate_with_retry(generate, model, question, instructions,
                        max_attempts=MAX_ATTEMPTS, delay=RETRY_DELAY_SECONDS,
                        sleep=time.sleep):
    """Retry only infrastructure failures; return (raw, attempts, errors)."""

    errors = []
    for attempt in range(1, max_attempts + 1):
        try:
            return generate(model, question, instructions), attempt, errors
        except Exception as exc:
            if not is_infrastructure_error(exc):
                raise
            errors.append(f"attempt {attempt}: {exc}")
            if attempt < max_attempts:
                sleep(delay)
    raise RuntimeError("Infrastructure failure after retries: "
                       + " | ".join(errors))


def plan_record(case, model, raw):
    """Parse and validate one raw model response (never retried)."""

    record = {"id": case["id"], "model": model, "raw_response": raw}
    try:
        plan = json.loads(raw)
        record["plan"] = plan
        validate_query_plan(plan)
        record["status"] = "valid_plan"
    except (ValueError, TypeError, KeyError) as exc:
        record["status"] = "invalid_plan"
        record["error"] = str(exc)
    return record


def generate_plans():
    from evaluation.ollama_query_plan_adapter import (
        build_instructions, generate_plan,
    )

    refuse_overwrite(PLANS_FILE)
    cases = load_cases()
    instructions = build_instructions()

    try:
        tags = requests.get("http://localhost:11434/api/tags", timeout=10).json()
        digests = {item["name"]: item.get("digest") for item in tags["models"]}
    except (requests.RequestException, ValueError, KeyError):
        digests = {}

    records = []
    for model in MODELS:
        for case in cases:
            print(f"{model} | {case['id']}", flush=True)
            try:
                raw, attempts, errors = generate_with_retry(
                    generate_plan, model, case["question"], instructions)
                record = plan_record(case, model, raw)
                record["attempts"] = attempts
                record["infrastructure_errors"] = errors
            except Exception as exc:
                record = {"id": case["id"], "model": model,
                          "status": "infrastructure_error", "error": str(exc)}
            print(f"  {record['status']} {record.get('error', '')}", flush=True)
            records.append(record)

    write_json(PLANS_FILE, {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "cases_file": CASES_FILE.name,
        "prompt_sha256": hashlib.sha256(instructions.encode("utf-8")).hexdigest(),
        "num_ctx": NUM_CTX,
        "temperature": 0,
        "model_digests": {model: digests.get(model) for model in MODELS},
        "records": records,
    })


# ----------------------------------------------------------------------
# Stage 3: execution and scoring
# ----------------------------------------------------------------------

def evaluate_case(case, record, reference, execute):
    """Score one model output for one case. ``execute`` maps DAX -> result."""

    requirement = case["requirement"]
    plan = record.get("plan") if record else None
    status = record.get("status") if record else "missing"
    result = {"id": case["id"], "category": case["category"],
              "plan_status": status}

    coverage = check_requirement_coverage(
        plan if status == "valid_plan" else None, requirement)
    result["coverage_findings"] = coverage["findings"]
    result["covered"] = coverage["covered"]

    if case["category"] == "unsupported":
        if status == "infrastructure_error":
            result.update(passed=False, outcome="infrastructure_error")
        else:
            answered = status == "valid_plan"
            result.update(passed=not answered,
                          outcome="answered" if answered else "not_answered")
        result["strict_passed"] = result["passed"]
        return result

    if reference is None or reference.get("status") != "success":
        raise ValueError(f"No successful reference for {case['id']}")

    if status != "valid_plan":
        result.update(passed=False, strict_passed=False,
                      outcome=status, error=record.get("error") if record else None)
        return result

    try:
        dax = compile_query_plan(plan)
        executed = execute(dax)
    except Exception as exc:
        result.update(passed=False, strict_passed=False,
                      outcome="execution_error", error=str(exc))
        return result

    candidate = {"columns": executed["columns"], "rows": executed["rows"]}
    comparator = (compare_results_ordered if requirement.get("order_by")
                  else compare_results_with_metric_alias)
    comparison = comparator(reference, candidate)

    result.update(
        candidate_dax=dax,
        row_count=executed["row_count"],
        columns=executed["columns"],
        passed=comparison["passed"],
        reason=comparison["reason"],
        outcome="evaluated",
        strict_passed=comparison["passed"] and coverage["covered"],
    )
    return result


def summarise(results):
    answerable = [item for item in results if item["category"] in ANSWERABLE]
    unsupported = [item for item in results if item["category"] == "unsupported"]

    def ratio(items, key):
        passed = sum(item[key] for item in items)
        return {"passed": passed, "total": len(items),
                "accuracy": passed / len(items) if items else None}

    return {
        "answerable_execution": ratio(answerable, "passed"),
        "answerable_strict": ratio(answerable, "strict_passed"),
        "unsupported_not_answered": ratio(unsupported, "passed"),
        "valid_plans": sum(item["plan_status"] == "valid_plan" for item in results),
    }


def evaluate():
    from src.powerbi_query_service import PowerBIQueryService

    refuse_overwrite(REPORT_FILE)
    cases = load_cases()
    references_payload = load_json(REFERENCES_FILE)
    references = {item["id"]: item for item in references_payload["cases"]}
    plans_payload = load_json(PLANS_FILE)
    records = {(item["model"], item["id"]): item
               for item in plans_payload["records"]}

    service = PowerBIQueryService()
    if service.dataset_id != references_payload["dataset_id"]:
        service.close()
        raise ValueError("Live dataset ID differs from reference dataset ID")

    per_model = {}
    try:
        for model in MODELS:
            results = []
            for case in cases:
                print(f"{model} | {case['id']}", flush=True)
                item = evaluate_case(case, records.get((model, case["id"])),
                                     references.get(case["id"]),
                                     service.execute_dax)
                results.append(item)
                flag = "PASS" if item["passed"] else "FAIL"
                strict = "" if item["strict_passed"] == item["passed"] else \
                    f" (strict {'PASS' if item['strict_passed'] else 'FAIL'})"
                print(f"  {flag}{strict} {item['outcome']} "
                      f"{item.get('reason', item.get('error', '')) or ''}",
                      flush=True)
                for finding in item["coverage_findings"]:
                    print(f"    coverage {finding['code']}: "
                          f"{finding['message']}", flush=True)
            per_model[model] = {"summary": summarise(results), "cases": results}
    finally:
        service.close()

    for model, payload in per_model.items():
        summary = payload["summary"]
        print(f"\n{model}")
        for key in ("answerable_execution", "answerable_strict",
                    "unsupported_not_answered"):
            item = summary[key]
            print(f"  {key}: {item['passed']}/{item['total']}")

    write_json(REPORT_FILE, {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_id": references_payload["dataset_id"],
        "source_cases": CASES_FILE.name,
        "source_plans": PLANS_FILE.name,
        "source_references": REFERENCES_FILE.name,
        "prompt_sha256": plans_payload.get("prompt_sha256"),
        "models": per_model,
    })


STAGES = {
    "references": capture_references,
    "generate": generate_plans,
    "evaluate": evaluate,
}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in STAGES:
        raise SystemExit(f"Usage: python -m evaluation.run_fresh_evaluation "
                         f"{{{'|'.join(STAGES)}}}")
    STAGES[argv[0]]()


if __name__ == "__main__":
    main()
