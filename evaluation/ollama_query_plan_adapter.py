import json
from pathlib import Path

import requests

from evaluation.ollama_adapter_v2 import load_schema
from evaluation.query_plan_validator import (
    load_model,
    validate_query_plan,
)
from evaluation.query_plan_compiler import compile_query_plan


ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "evaluation"
RESULTS = EVAL / "results"

MODELS = ["qwen2.5-coder:3b", "qwen2.5-coder:7b"]


def generate_plan(model, question, instructions):
    response = requests.post(
        "http://localhost:11434/api/chat",
        json={
            "model": model,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0,
                "num_ctx": 8192,
            },
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": question},
            ],
        },
        timeout=240,
    )

    response.raise_for_status()

    return response.json()["message"]["content"].strip()


def main():
    cases = json.loads(
        (EVAL / "holdout_cases.json").read_text(
            encoding="utf-8-sig"
        )
    )

    contract = json.loads(
        (EVAL / "query_plan_schema.json").read_text(
            encoding="utf-8-sig"
        )
    )

    prompt = (EVAL / "query_plan_prompt_v1.txt").read_text(
        encoding="utf-8-sig"
    )

    model_schema = load_model()

    relationships = "\n".join(
        f"{r['PK']} -> {r['FK']}"
        for r in model_schema["ActiveRelationships"]
    )

    instructions = (
        prompt
        + "\n\nSEMANTIC MODEL:\n"
        + load_schema()
        + "\n\nRELATIONSHIPS:\n"
        + relationships
        + "\n\nQUERY PLAN JSON SCHEMA:\n"
        + json.dumps(contract, separators=(",", ":"))
    )

    RESULTS.mkdir(parents=True, exist_ok=True)

    output_file = RESULTS / "ollama_query_plans_v1.json"

    if output_file.exists():
        raise FileExistsError(
            f"Results already exist: {output_file}"
        )

    outputs = []

    for model in MODELS:
        for case in cases:
            print(
                f"Planning {case['id']} with {model}...",
                flush=True,
            )

            record = {
                "id": case["id"],
                "model": model,
                "question": case["question"],
            }

            try:
                raw = generate_plan(
                    model,
                    case["question"],
                    instructions,
                )

                record["raw_response"] = raw

                plan = json.loads(raw)
                record["plan"] = plan

                validate_query_plan(plan)

                record["candidate_dax"] = compile_query_plan(plan)
                record["status"] = "valid_plan"

            except Exception as exc:
                record["status"] = "error"
                record["error"] = str(exc)

            outputs.append(record)

            print(
                f"  Status: {record['status']}",
                flush=True,
            )

            if record["status"] == "error":
                print(f"  Error: {record['error']}", flush=True)

    output_file.write_text(
        json.dumps(
            outputs,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(f"\nResults saved to: {output_file}")


if __name__ == "__main__":
    main()
