import json

import pytest
import requests

from evaluation import ollama_query_plan_adapter as adapter


VALID_PLAN = {
    "metrics": [
        {
            "alias": "Net Sales",
            "expression": {"kind": "measure", "name": "Net Sales"},
        }
    ],
    "group_by": [{"table": "DimStore", "column": "Region"}],
    "filters": [],
    "order_by": [{"field": "Net Sales", "direction": "desc"}],
    "limit": 5,
}


class FakeResponse:
    def __init__(self, content, status_code=200):
        self._content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return {"message": {"content": self._content}}


@pytest.fixture
def eval_dir(tmp_path, monkeypatch):
    """Point the adapter at an isolated evaluation/results directory."""
    eval_dir = tmp_path / "evaluation"
    eval_dir.mkdir()

    (eval_dir / "holdout_cases.json").write_text(
        json.dumps([
            {"id": "case-1", "question": "Top regions by net sales"},
            {"id": "case-2", "question": "Something the model botches"},
        ]),
        encoding="utf-8",
    )
    (eval_dir / "query_plan_schema.json").write_text(
        json.dumps({"type": "object"}), encoding="utf-8"
    )
    (eval_dir / "query_plan_prompt_v1.txt").write_text(
        "PROMPT HEADER", encoding="utf-8"
    )

    monkeypatch.setattr(adapter, "EVAL", eval_dir)
    monkeypatch.setattr(adapter, "RESULTS", eval_dir / "results")
    monkeypatch.setattr(adapter, "MODELS", ["fake-model"])
    return eval_dir


def test_generate_plan_posts_expected_payload(monkeypatch):
    captured = {}

    def fake_post(url, json, timeout):
        captured.update(url=url, payload=json, timeout=timeout)
        return FakeResponse('  {"metrics": []}\n')

    monkeypatch.setattr(adapter.requests, "post", fake_post)

    result = adapter.generate_plan("m", "question?", "system text")

    assert result == '{"metrics": []}'
    assert captured["url"] == "http://localhost:11434/api/chat"
    payload = captured["payload"]
    assert payload["model"] == "m"
    assert payload["stream"] is False
    assert payload["format"] == "json"
    assert payload["options"]["temperature"] == 0
    assert payload["messages"] == [
        {"role": "system", "content": "system text"},
        {"role": "user", "content": "question?"},
    ]


def test_generate_plan_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(
        adapter.requests,
        "post",
        lambda *a, **k: FakeResponse("", status_code=500),
    )

    with pytest.raises(requests.HTTPError):
        adapter.generate_plan("m", "q", "i")


def test_main_records_valid_and_error_cases(eval_dir, monkeypatch):
    seen_instructions = []

    def fake_generate(model, question, instructions):
        seen_instructions.append(instructions)
        if question.startswith("Top regions"):
            return json.dumps(VALID_PLAN)
        return "not json at all"

    monkeypatch.setattr(adapter, "generate_plan", fake_generate)

    adapter.main()

    output_file = eval_dir / "results" / "ollama_query_plans_v1.json"
    records = json.loads(output_file.read_text(encoding="utf-8"))

    assert [r["id"] for r in records] == ["case-1", "case-2"]

    ok, bad = records
    assert ok["status"] == "valid_plan"
    assert ok["model"] == "fake-model"
    assert ok["plan"] == VALID_PLAN
    assert "EVALUATE" in ok["candidate_dax"]

    assert bad["status"] == "error"
    assert bad["raw_response"] == "not json at all"
    assert "error" in bad and "candidate_dax" not in bad

    instructions = seen_instructions[0]
    assert instructions.startswith("PROMPT HEADER")
    assert "SEMANTIC MODEL:" in instructions
    assert "RELATIONSHIPS:" in instructions
    assert 'QUERY PLAN JSON SCHEMA:\n{"type":"object"}' in instructions


def test_main_records_validation_failure(eval_dir, monkeypatch):
    bad_plan = json.loads(json.dumps(VALID_PLAN))
    bad_plan["metrics"][0]["expression"]["name"] = "Imaginary Sales"
    monkeypatch.setattr(
        adapter, "generate_plan", lambda *a: json.dumps(bad_plan)
    )

    adapter.main()

    records = json.loads(
        (eval_dir / "results" / "ollama_query_plans_v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert all(r["status"] == "error" for r in records)
    assert all("Unknown measure" in r["error"] for r in records)
    assert all("candidate_dax" not in r for r in records)


def test_main_records_transport_errors(eval_dir, monkeypatch):
    def boom(*args):
        raise requests.ConnectionError("ollama is down")

    monkeypatch.setattr(adapter, "generate_plan", boom)

    adapter.main()

    records = json.loads(
        (eval_dir / "results" / "ollama_query_plans_v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert all(r["status"] == "error" for r in records)
    assert all(r["error"] == "ollama is down" for r in records)
    assert all("raw_response" not in r for r in records)


def test_main_refuses_to_overwrite_existing_results(eval_dir, monkeypatch):
    results = eval_dir / "results"
    results.mkdir()
    existing = results / "ollama_query_plans_v1.json"
    existing.write_text("[]", encoding="utf-8")

    def should_not_run(*args):
        raise AssertionError("generate_plan should not be called")

    monkeypatch.setattr(adapter, "generate_plan", should_not_run)

    with pytest.raises(FileExistsError):
        adapter.main()

    assert existing.read_text(encoding="utf-8") == "[]"
