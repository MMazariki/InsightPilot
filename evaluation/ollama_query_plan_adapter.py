import json
from pathlib import Path

import requests

from evaluation.query_plan_validator import (
    load_model,
    validate_query_plan,
)
from evaluation.query_plan_compiler import compile_query_plan


ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "evaluation"
RESULTS = EVAL / "results"

MODELS = ["qwen2.5-coder:3b", "qwen2.5-coder:7b"]


def load_schema():
    """Describe the semantic model for the prompt.

    Same text format as ``ollama_adapter_v2.load_schema`` but sourced from the
    committed schema snapshot, so prompts are reproducible from a clean clone.
    """

    lines = []
    for table in load_model()["Tables"]:
        columns = [c["Name"] for c in table.get("Columns", [])]
        measures = [m["Name"] for m in table.get("Measures", [])]

        lines.append(
            f"Table {table['Name']}: "
            f"Columns: {', '.join(columns)}; "
            f"Measures: {', '.join(measures)}"
        )

    return "\n".join(lines)


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


PROMPT_FILES = {
    "v1": "query_plan_prompt_v1.txt",
    "v2": "query_plan_prompt_v2.txt",
    "v3": "query_plan_prompt_v3.txt",
    "v4": "query_plan_prompt_v4.txt",
    # v5 changes the pipeline (semantic rule 3), not the prompt text.
    "v5": "query_plan_prompt_v4.txt",
    # v6 changes repair wording, hints and alias normalisation, not the prompt.
    "v6": "query_plan_prompt_v4.txt",
    # v7 adds expression-kind hints (rule 0b); prompt text unchanged.
    "v7": "query_plan_prompt_v4.txt",
}


def build_instructions(prompt_version="v1"):
    """Build the planner system prompt (prompt + model + output contracts).

    v1: query plan only (unchanged). v2: adds the clarification contract.
    """

    contract = json.loads(
        (EVAL / "query_plan_schema.json").read_text(
            encoding="utf-8-sig"
        )
    )

    prompt = (EVAL / PROMPT_FILES[prompt_version]).read_text(
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

    if prompt_version != "v1":
        clarification = json.loads(
            (EVAL / "clarification_schema.json").read_text(
                encoding="utf-8-sig"
            )
        )
        instructions += (
            "\n\nCLARIFICATION JSON SCHEMA:\n"
            + json.dumps(clarification, separators=(",", ":"))
        )

    if prompt_version not in ("v1", "v2", "v3"):
        from evaluation.semantic_checks import describe_catalog

        instructions += "\n\nCOLUMN VALUES:\n" + describe_catalog()

    return instructions


def generate_messages(model, messages):
    """Chat call with an explicit message list (used for one repair turn)."""

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
            "messages": messages,
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

    instructions = build_instructions()

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
