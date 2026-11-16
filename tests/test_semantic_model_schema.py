import json
from pathlib import Path

import pytest

from evaluation import ollama_query_plan_adapter as adapter
from evaluation.query_plan_validator import MODEL_SCHEMA_FILE, load_model

ROOT = Path(__file__).resolve().parents[1]
LIVE_PROBE_FILE = ROOT / "fabric_iq_schema.json"

needs_live_probe = pytest.mark.skipif(
    not LIVE_PROBE_FILE.exists(),
    reason="Local Fabric IQ probe output (gitignored) not present",
)


def load_live_schema():
    wrapper = json.loads(LIVE_PROBE_FILE.read_text(encoding="utf-8-sig"))
    return json.loads(wrapper["content"][0]["text"])["schema"]


def test_snapshot_is_committed_and_well_formed():
    model = load_model()
    assert set(model) == {"Tables", "ActiveRelationships"}
    names = {table["Name"] for table in model["Tables"]}
    assert {"FactSales", "DimDate", "DimProduct", "DimStore",
            "DimCustomer"} <= names


def test_snapshot_contains_no_identifiers():
    text = MODEL_SCHEMA_FILE.read_text(encoding="utf-8-sig")
    for marker in ("semanticModel", "ArtifactId", "http", "groups/"):
        assert marker not in text


@needs_live_probe
def test_snapshot_matches_live_probe():
    assert load_model() == load_live_schema(), (
        "semantic_model_schema.json has drifted from fabric_iq_schema.json; "
        "regenerate the snapshot and re-run the evaluation suites."
    )


@needs_live_probe
def test_adapter_prompt_schema_unchanged_from_v2():
    from evaluation.ollama_adapter_v2 import load_schema as legacy_load_schema

    assert adapter.load_schema() == legacy_load_schema()
