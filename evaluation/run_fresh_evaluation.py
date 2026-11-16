"""Fresh held-out evaluation of the structured planning pipeline.

Run from the project root, one stage at a time:

    python -m evaluation.run_fresh_evaluation references [--set S]
    python -m evaluation.run_fresh_evaluation generate   [--set S] [--prompt P]
    python -m evaluation.run_fresh_evaluation evaluate   [--set S] [--prompt P]

``references`` and ``evaluate`` call Power BI (read-only); ``generate`` calls
local Ollama only. Sets: fresh_v1 (default), fresh_v2. Prompts: v1 (default;
plan only, no request guard) and v2 (plan OR clarification, with the
pre-planner write-request guard).

Cases, requirement specs and reference DAX live in evaluation/fresh_cases_*
files and were committed before any generation on that set. No stage calls Azure AI Foundry or a
paid API, and no stage overwrites an existing result file.

Scoring per model:
  * answerable (incl. empty_result): execution pass = valid plan, compiled
    from JSON, executed, results equivalent to reference (row order also
    checked when the requirement specifies ordering). Strict pass =
    execution pass AND requirement coverage.
  * unsupported: ``passed`` = no valid plan produced (comparable across
    prompts); ``declined`` = a deliberate clarification (planner or guard).
    Under v1 there is no clarification channel, so declined is always 0.
  * a clarification on an answerable case is an over-refusal (fails).
"""

import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from evaluation.alias_aware_comparator import (
    compare_results_with_metric_alias,
    compare_results_with_metric_aliases,
)
from evaluation.ordered_comparator import compare_results_ordered
from evaluation.planner_response import parse_planner_response
from evaluation.query_plan_compiler import compile_query_plan
from evaluation.query_plan_validator import validate_query_plan
from evaluation.request_guard import screen_request
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
DECLINED_STATUSES = ("clarification", "blocked_by_guard")
SETS = {
    "fresh_v1": "fresh_cases_v1.json",
    "fresh_v2": "fresh_cases_v2.json",
    "fresh_v3": "fresh_cases_v3.json",
    "fresh_v4": "fresh_cases_v4.json",
    "fresh_v5": "fresh_cases_v5.json",
    "fresh_v6": "fresh_cases_v6.json",
    "fresh_v7": "fresh_cases_v7.json",
    "fresh_v8": "fresh_cases_v8.json",
}
PROMPTS = ("v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8")
# Prompts whose pipeline adds semantic checks and one validator-feedback
# repair attempt for rejected plans.
REPAIR_PROMPTS = {"v4", "v5", "v6", "v7", "v8"}
REPAIR_INSTRUCTION = (
    "Your previous response was rejected: {error}\n"
    "Return a corrected JSON object: either a query plan that fixes this "
    "problem or a clarification object. Return JSON only."
)
# Pipeline v6: prefer fixing the plan; decline only if the question itself
# cannot be answered (v4/v5 wording led 3B to decline answerable questions).
REPAIR_INSTRUCTION_V6 = (
    "Your previous response was rejected: {error}\n"
    "Fix the query plan so that it answers the user's question with the "
    "SEMANTIC MODEL. Only return a clarification object if the question "
    "itself cannot be answered from the model; a fixable mistake in your plan "
    "is not a reason to decline. Return JSON only."
)


def semantic_level(prompt):
    """Semantic checks per pipeline: none (v1-v3), v4 rules, v4 + rule 3,
    v4 + rule 3 + rule 0 hints and alias normalisation (v6), v6 + rule 0b
    expression-kind hints (v7)."""

    if prompt in ("v5", "v6", "v7", "v8"):
        return prompt
    return prompt in REPAIR_PROMPTS


def repair_instruction(prompt):
    return (REPAIR_INSTRUCTION_V6 if prompt in ("v6", "v7", "v8")
            else REPAIR_INSTRUCTION)

# Comparator per case set is fixed so every arm on a set is scored by the same
# rule. fresh_v1/fresh_v2 were scored with the single-alias comparator; later
# sets tolerate several renamed metrics (values remain strict).
MULTI_ALIAS_SETS = {"fresh_v3", "fresh_v4", "fresh_v5", "fresh_v6", "fresh_v7",
                    "fresh_v8"}


def comparators_for(set_name):
    base = (compare_results_with_metric_aliases if set_name in MULTI_ALIAS_SETS
            else compare_results_with_metric_alias)

    def ordered(expected, actual):
        return compare_results_ordered(expected, actual, base_comparator=base)

    return base, ordered


def resolve_paths(set_name="fresh_v1", prompt="v1"):
    """Input/output paths for a (case set, prompt) arm.

    The original fresh_v1 + v1 arm keeps its legacy file names (and honours
    the module-level constants, so tests can redirect them).
    """

    if set_name not in SETS or prompt not in PROMPTS:
        raise ValueError(f"Unknown set/prompt: {set_name}/{prompt}")

    if set_name == "fresh_v1":
        references = REFERENCES_FILE
        cases = CASES_FILE
    else:
        references = RESULTS / f"{set_name}_reference_results.json"
        cases = EVAL / SETS[set_name]

    if (set_name, prompt) == ("fresh_v1", "v1"):
        plans, report = PLANS_FILE, REPORT_FILE
    else:
        plans = RESULTS / f"{set_name}_prompt_{prompt}_query_plans.json"
        report = RESULTS / f"{set_name}_prompt_{prompt}_evaluation.json"

    return {"cases": cases, "references": references,
            "plans": plans, "report": report, "set": set_name}


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_cases(path=None):
    cases = load_json(path or CASES_FILE)["cases"]
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

def capture_references(paths):
    from src.powerbi_query_service import PowerBIQueryService

    refuse_overwrite(paths["references"])
    cases = [case for case in load_cases(paths["cases"])
             if case["category"] in ANSWERABLE]
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

    write_json(paths["references"], output)


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


def plan_record(case, model, raw, prompt="v1"):
    """Parse and validate one raw model response (never retried).

    Under v1 only query plans are accepted (original behaviour). Under v2 a
    schema-valid clarification is recorded as ``clarification``.
    """

    record = {"id": case["id"], "model": model, "raw_response": raw}
    try:
        payload = json.loads(raw)
        record["plan"] = payload
        if prompt == "v1":
            validate_query_plan(payload)
            record["status"] = "valid_plan"
        else:
            parsed = parse_planner_response(
                payload, semantic_checks=semantic_level(prompt))
            if parsed["kind"] == "plan":
                record["status"] = "valid_plan"
                if parsed.get("normalizations"):
                    # Keep the model's original output in raw_response and
                    # store the normalised plan that is compiled/executed.
                    record["plan"] = parsed["plan"]
                    record["normalizations"] = parsed["normalizations"]
            else:
                del record["plan"]
                record["clarification"] = {"reason": parsed["reason"],
                                           "message": parsed["message"]}
                record["status"] = "clarification"
    except (ValueError, TypeError, KeyError) as exc:
        record["status"] = "invalid_plan"
        record["error"] = str(exc)
    return record


def attempt_repair(case, model, instructions, record, prompt, chat,
                   sleep=time.sleep):
    """One bounded repair turn for a rejected (invalid) plan.

    Only validator/semantic-check rejections are repaired; valid plans are
    never second-guessed. ``chat(model, messages)`` returns raw text.
    """

    messages = [
        {"role": "system", "content": instructions},
        {"role": "user", "content": case["question"]},
        {"role": "assistant", "content": record.get("raw_response", "")},
        {"role": "user",
         "content": repair_instruction(prompt).format(error=record.get("error"))},
    ]

    def call(model_name, question, system):
        return chat(model_name, messages)

    raw, attempts, errors = generate_with_retry(call, model, None, None,
                                                sleep=sleep)
    repaired = plan_record(case, model, raw, prompt)
    repaired["attempts"] = record.get("attempts", 1)
    repaired["infrastructure_errors"] = record.get("infrastructure_errors", [])
    repaired["repair"] = {
        "first_raw_response": record.get("raw_response"),
        "first_error": record.get("error"),
        "repair_attempts": attempts,
        "repair_infrastructure_errors": errors,
        "succeeded": repaired["status"] != "invalid_plan",
    }
    return repaired


def generate_plans(paths, prompt="v1"):
    from evaluation.ollama_query_plan_adapter import (
        build_instructions, generate_messages, generate_plan,
    )

    refuse_overwrite(paths["plans"])
    cases = load_cases(paths["cases"])
    instructions = build_instructions(prompt)
    use_guard = prompt != "v1"

    try:
        tags = requests.get("http://localhost:11434/api/tags", timeout=10).json()
        digests = {item["name"]: item.get("digest") for item in tags["models"]}
    except (requests.RequestException, ValueError, KeyError):
        digests = {}

    records = []
    for model in MODELS:
        for case in cases:
            print(f"{model} | {case['id']}", flush=True)
            blocked = screen_request(case["question"]) if use_guard else None
            if blocked is not None:
                records.append({"id": case["id"], "model": model,
                                "status": "blocked_by_guard",
                                "clarification": blocked["clarification"]})
                print("  blocked_by_guard", flush=True)
                continue
            try:
                raw, attempts, errors = generate_with_retry(
                    generate_plan, model, case["question"], instructions)
                record = plan_record(case, model, raw, prompt)
                record["attempts"] = attempts
                record["infrastructure_errors"] = errors
                if (prompt in REPAIR_PROMPTS
                        and record["status"] == "invalid_plan"):
                    print(f"  rejected, repairing: {record['error']}",
                          flush=True)
                    record = attempt_repair(case, model, instructions,
                                            record, prompt, generate_messages)
            except Exception as exc:
                record = {"id": case["id"], "model": model,
                          "status": "infrastructure_error", "error": str(exc)}
            print(f"  {record['status']} {record.get('error', '')}", flush=True)
            records.append(record)

    write_json(paths["plans"], {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "cases_file": paths["cases"].name,
        "prompt_version": prompt,
        "request_guard": use_guard,
        "semantic_checks_and_repair": prompt in REPAIR_PROMPTS,
        "semantic_level": semantic_level(prompt),
        "prompt_sha256": hashlib.sha256(instructions.encode("utf-8")).hexdigest(),
        "num_ctx": NUM_CTX,
        "temperature": 0,
        "model_digests": {model: digests.get(model) for model in MODELS},
        "records": records,
    })


# ----------------------------------------------------------------------
# Stage 3: execution and scoring
# ----------------------------------------------------------------------

def evaluate_case(case, record, reference, execute, set_name="fresh_v1"):
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

    declined = status in DECLINED_STATUSES
    result["declined"] = declined
    if declined:
        result["clarification_reason"] = (
            (record.get("clarification") or {}).get("reason"))

    if case["category"] == "unsupported":
        if status == "infrastructure_error":
            result.update(passed=False, outcome="infrastructure_error")
        elif declined:
            result.update(passed=True, outcome="declined")
        else:
            answered = status == "valid_plan"
            result.update(passed=not answered,
                          outcome="answered" if answered else "not_answered")
        result["strict_passed"] = result["passed"]
        expected = case.get("expected_reason")
        if declined and expected:
            result["reason_matches"] = result["clarification_reason"] == expected
        return result

    if declined:
        result.update(passed=False, strict_passed=False,
                      outcome="over_refusal")
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
    unordered, ordered = comparators_for(set_name)
    comparator = ordered if requirement.get("order_by") else unordered
    comparison = comparator(reference, candidate)

    result.update(
        candidate_dax=dax,
        row_count=executed["row_count"],
        columns=executed["columns"],
        rows=executed["rows"],
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
        passed = sum(bool(item.get(key)) for item in items)
        return {"passed": passed, "total": len(items),
                "accuracy": passed / len(items) if items else None}

    return {
        "answerable_execution": ratio(answerable, "passed"),
        "answerable_strict": ratio(answerable, "strict_passed"),
        "unsupported_not_answered": ratio(unsupported, "passed"),
        "unsupported_declined": ratio(unsupported, "declined"),
        "answerable_over_refusals": sum(
            item.get("outcome") == "over_refusal" for item in answerable),
        "valid_plans": sum(item["plan_status"] == "valid_plan" for item in results),
    }


def evaluate(paths):
    from src.powerbi_query_service import PowerBIQueryService

    refuse_overwrite(paths["report"])
    cases = load_cases(paths["cases"])
    references_payload = load_json(paths["references"])
    references = {item["id"]: item for item in references_payload["cases"]}
    plans_payload = load_json(paths["plans"])
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
                                     service.execute_dax,
                                     paths.get("set", "fresh_v1"))
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
                    "unsupported_not_answered", "unsupported_declined"):
            item = summary[key]
            print(f"  {key}: {item['passed']}/{item['total']}")
        print(f"  answerable_over_refusals: "
              f"{summary['answerable_over_refusals']}")

    write_json(paths["report"], {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_id": references_payload["dataset_id"],
        "source_cases": paths["cases"].name,
        "source_plans": paths["plans"].name,
        "source_references": paths["references"].name,
        "prompt_version": plans_payload.get("prompt_version", "v1"),
        "request_guard": plans_payload.get("request_guard", False),
        "prompt_sha256": plans_payload.get("prompt_sha256"),
        "comparator": comparators_for(paths.get("set", "fresh_v1"))[0].__name__,
        "models": per_model,
    })


STAGES = ("references", "generate", "evaluate")


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m evaluation.run_fresh_evaluation")
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("--set", dest="set_name", choices=sorted(SETS),
                        default="fresh_v1")
    parser.add_argument("--prompt", choices=PROMPTS, default="v1")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    paths = resolve_paths(args.set_name, args.prompt)
    if args.stage == "references":
        capture_references(paths)
    elif args.stage == "generate":
        generate_plans(paths, args.prompt)
    else:
        evaluate(paths)


if __name__ == "__main__":
    main()
