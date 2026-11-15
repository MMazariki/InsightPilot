import json
import re
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "evaluation"
RESULTS = EVAL / "results"

MODELS = ["qwen2.5-coder:3b", "qwen2.5-coder:7b"]


def load_schema():
    raw = json.loads(
        (ROOT / "fabric_iq_schema.json").read_text(encoding="utf-8-sig")
    )
    model = json.loads(raw["content"][0]["text"])["schema"]

    lines = []
    for table in model["Tables"]:
        columns = [c["Name"] for c in table.get("Columns", [])]
        measures = [m["Name"] for m in table.get("Measures", [])]

        lines.append(
            f"Table {table['Name']}: "
            f"Columns: {', '.join(columns)}; "
            f"Measures: {', '.join(measures)}"
        )

    return "\n".join(lines)


def generate_dax(model, question, schema):
    prompt_template = (EVAL / "dax_prompt_v2.txt").read_text(encoding="utf-8-sig")
    system_prompt = f"{prompt_template}\n\nSEMANTIC MODEL:\n{schema}"

    response = requests.post(
        "http://localhost:11434/api/chat",
        json={
            "model": model,
            "stream": False,
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
        },
        timeout=240,
    )

    response.raise_for_status()
    return response.json()["message"]["content"].strip()


def main():
    cases = json.loads(
        (EVAL / "cases.json").read_text(encoding="utf-8-sig")
    )
    schema = load_schema()

    RESULTS.mkdir(parents=True, exist_ok=True)
    output_file = RESULTS / "ollama_generations_v2.json"

    if output_file.exists():
        raise FileExistsError(f"Results already exist: {output_file}")

    outputs = []

    for model in MODELS:
        for case in cases:
            print(f"Generating {case['id']} with {model}...", flush=True)

            try:
                raw = generate_dax(model, case["question"], schema)

                dax = re.sub(
                    r"^```(?:dax)?\s*|\s*```$",
                    "",
                    raw,
                    flags=re.IGNORECASE,
                ).strip()

                starts_with_evaluate = bool(
                    re.match(r"^EVALUATE\b", dax, re.IGNORECASE)
                )

                outputs.append({
                    "id": case["id"],
                    "model": model,
                    "question": case["question"],
                    "raw_response": raw,
                    "candidate_dax": dax,
                    "starts_with_evaluate": starts_with_evaluate,
                    "status": "generated",
                })

            except Exception as exc:
                outputs.append({
                    "id": case["id"],
                    "model": model,
                    "status": "error",
                    "error": str(exc),
                })

    output_file.write_text(
        json.dumps(outputs, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"\nResults saved to: {output_file}")


if __name__ == "__main__":
    main()

