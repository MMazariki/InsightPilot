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


# The snapshot was first taken from the Fabric IQ probe (fabric_iq_schema.json)
# and compared with it here. Since 2026-11-17 it is kept in step with the
# published dataset by scripts/sync_model_schema.py instead, so the probe
# comparisons were removed.


def test_sync_reports_and_applies_model_changes():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "sync_model_schema", ROOT / "scripts" / "sync_model_schema.py")
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)

    schema = {"Tables": [{"Name": "FactSales",
                          "Columns": [{"Name": "Quantity", "Type": "Integer"}],
                          "Measures": [{"Name": "Net Sales", "Type": "Double"}]}],
              "ActiveRelationships": []}
    live_columns = [("FactSales", "TotalQuantity", "Integer")]
    live_measures = [("FactSales", "Net Sales", "Double"),
                     ("FactSales", "Cost", "Double"),
                     ("FactSales", "Orders", "Integer")]
    diff = sync.compare(schema, live_columns, live_measures, skip={"Orders"})
    assert diff["new_columns"] == [("FactSales", "TotalQuantity", "Integer")]
    assert diff["gone_columns"] == [("FactSales", "Quantity")]
    assert diff["new_measures"] == [("FactSales", "Cost", "Double")]
    table = sync.apply(schema, diff)["Tables"][0]
    assert [c["Name"] for c in table["Columns"]] == ["TotalQuantity"]
    assert [m["Name"] for m in table["Measures"]] == ["Net Sales", "Cost"]
    assert sync._is_auto("LocalDateTable_77ea")
