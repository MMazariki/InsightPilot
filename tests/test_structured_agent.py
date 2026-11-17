import json

import pytest

from src.structured_agent import (
    StructuredPlanningAgent,
    format_result_table,
)

PLAN = {
    "metrics": [{"alias": "Net Sales",
                 "expression": {"kind": "measure", "name": "Net Sales"}}],
    "group_by": [{"table": "DimStore", "column": "Region"}],
    "filters": [], "order_by": [], "limit": None,
}


class FakePowerBI:
    def __init__(self, result=None, error=None):
        self.result = result or {
            "columns": ["DimStore[Region]", "[Net Sales]"],
            "rows": [{"DimStore[Region]": "Central", "[Net Sales]": 1234.5}],
            "row_count": 1,
        }
        self.error = error
        self.queries = []
        self.closed = False

    def execute_dax(self, dax):
        self.queries.append(dax)
        if self.error:
            raise self.error
        return self.result

    def close(self):
        self.closed = True


def make_agent(responses, powerbi=None, chat_responses=None):
    calls = {"generate": 0, "chat": []}
    outputs = list(responses)
    chat_outputs = list(chat_responses or [])

    def generate(model, question, instructions):
        calls["generate"] += 1
        assert "COLUMN VALUES:" in instructions  # v6 prompt (= v4 text)
        return outputs.pop(0)

    def chat(model, messages):
        calls["chat"].append(messages)
        return chat_outputs.pop(0)

    agent = StructuredPlanningAgent(model="fake", powerbi=powerbi or FakePowerBI(),
                                    generate=generate, chat=chat)
    return agent, calls


def test_successful_question_executes_compiled_dax_only():
    powerbi = FakePowerBI()
    agent, calls = make_agent([json.dumps(PLAN)], powerbi)
    answer = agent.ask("Show net sales by store region.")
    assert calls["generate"] == 1 and not calls["chat"]
    assert len(powerbi.queries) == 1
    assert powerbi.queries[0].startswith("EVALUATE")
    assert "| Region | Net Sales |" in answer
    assert "| Central | 1,234.50 |" in answer
    assert "```dax" in answer
    assert agent.last_trace["stages"] == ["planner", "compiled", "executed"]
    assert agent.pending_visual_request is None


def test_guard_blocks_without_model_or_execution():
    powerbi = FakePowerBI()
    agent, calls = make_agent([], powerbi)
    answer = agent.ask("Delete all sales transactions from 2025.")
    assert calls["generate"] == 0
    assert powerbi.queries == []
    assert "No query was executed" in answer
    assert agent.last_trace["stages"][0] == "request_guard:blocked"


def test_planner_clarification_is_returned_without_execution():
    powerbi = FakePowerBI()
    clarification = {"clarification": {"reason": "undefined_metric",
                                       "message": "There is no churn measure."}}
    agent, _ = make_agent([json.dumps(clarification)], powerbi)
    answer = agent.ask("What is churn?")
    assert answer.startswith("There is no churn measure.")
    assert powerbi.queries == []


def test_rejected_plan_is_repaired_once_with_the_message():
    bad = dict(PLAN, metrics=[{"alias": "Channel", "expression": {
        "kind": "measure", "name": "Channel"}}])
    powerbi = FakePowerBI()
    agent, calls = make_agent([json.dumps(bad)], powerbi,
                              chat_responses=[json.dumps(PLAN)])
    agent.ask("Show net sales by region.")
    assert len(calls["chat"]) == 1
    assert "is not a measure" in calls["chat"][0][-1]["content"]
    assert agent.last_trace["stages"] == [
        "planner", "rejected", "repair", "compiled", "executed"]
    assert len(powerbi.queries) == 1


def test_failed_repair_executes_nothing():
    bad = json.dumps(dict(PLAN, metrics=[]))
    powerbi = FakePowerBI()
    agent, calls = make_agent([bad], powerbi, chat_responses=[bad])
    answer = agent.ask("Show something.")
    assert len(calls["chat"]) == 1
    assert powerbi.queries == []
    assert "nothing was executed" in answer
    assert "failed_validation" in agent.last_trace["stages"]


def test_execution_error_is_reported_with_dax():
    powerbi = FakePowerBI(error=RuntimeError("HTTP 400"))
    agent, _ = make_agent([json.dumps(PLAN)], powerbi)
    answer = agent.ask("Show net sales by store region.")
    assert "Power BI returned an error: HTTP 400" in answer
    assert "```dax" in answer


def test_alias_normalisation_recorded_in_trace():
    plan = json.loads(json.dumps(PLAN))
    plan["metrics"][0]["alias"] = "Net Sales, total"
    agent, _ = make_agent([json.dumps(plan)])
    agent.ask("Show net sales by store region.")
    assert agent.last_trace["normalizations"][0]["to"] == "Net Sales total"


def test_close_closes_powerbi():
    powerbi = FakePowerBI()
    agent, _ = make_agent([], powerbi)
    agent.close()
    assert powerbi.closed


def test_result_table_formatting_and_truncation():
    rows = [{"[A]": i, "T[B]": "x|y"} for i in range(60)]
    table = format_result_table(["T[B]", "[A]"], rows)
    assert table.splitlines()[0] == "| B | A |"
    assert "x\\|y" in table
    assert "Showing the first 50 of 60 rows" in table
    assert format_result_table([], []).startswith("_No data matches")


@pytest.mark.parametrize("mode, expected", [
    ("structured", "StructuredPlanningAgent"),
    ("foundry", "PowerBIAgent"),
])
def test_main_selects_agent_by_environment(monkeypatch, mode, expected):
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[1].joinpath("src", "main.py").read_text(
        encoding="utf-8")
    tree = ast.parse(source)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert expected in names
    assert 'os.environ.get("INSIGHTPILOT_AGENT", "foundry")' in source
    assert "powerbi_embed_component(" in source


# ------------------------------------------------------------------
# Answerability gate (pipeline v9)
# ------------------------------------------------------------------

def test_gate_blocks_unfaithful_plan_without_execution():
    powerbi = FakePowerBI()
    verdict = json.dumps({"faithful": False, "reason": "no forecasting"})
    agent, calls = make_agent([json.dumps(PLAN)], powerbi,
                              chat_responses=[verdict])
    answer = agent.ask("Forecast net sales by store region.")
    assert powerbi.queries == []
    assert "can't answer this faithfully" in answer
    assert "forecast" in answer
    assert "No query was executed" in answer
    assert agent.last_trace["stages"][-2:] == [
        "answerability:blocked", "clarification:undefined_metric"]
    assert calls["chat"][0][0]["content"].startswith("You check whether")


def test_gate_passes_faithful_plan_with_unknown_word():
    powerbi = FakePowerBI()
    verdict = json.dumps({"faithful": True, "reason": "same metric"})
    agent, _ = make_agent([json.dumps(PLAN)], powerbi, chat_responses=[verdict])
    agent.ask("Give a rundown of net sales by store region.")
    assert len(powerbi.queries) == 1
    assert agent.last_trace["stages"] == [
        "planner", "answerability:pass", "compiled", "executed"]
    assert agent.last_trace["answerability"]["unknown_terms"] == ["rundown"]


def test_gate_not_called_for_in_vocabulary_question():
    agent, calls = make_agent([json.dumps(PLAN)])
    agent.ask("Show net sales by store region.")
    assert calls["chat"] == []


def test_gate_blocks_question_that_names_no_metric():
    # Pipeline v11: every word is in the vocabulary, but no metric is named.
    powerbi = FakePowerBI()
    verdict = json.dumps({"faithful": False, "reason": "No metric named."})
    agent, calls = make_agent([json.dumps(PLAN)], powerbi,
                              chat_responses=[verdict])
    answer = agent.ask("Show the breakdown.")
    assert powerbi.queries == []
    assert "Which metric do you mean" in answer
    assert agent.last_trace["answerability"] == {
        "unknown_terms": [], "no_metric_reference": True,
        "reason": "No metric named."}
    assert "Note: The question does not name any metric" in (
        calls["chat"][0][1]["content"])


def test_agent_accepts_grouped_column_echo_without_repair():
    # Live v9 failure: "Show gross profit by sales channel".
    plan = {
        "metrics": [
            {"alias": "Gross Profit",
             "expression": {"kind": "measure", "name": "Gross Profit"}},
            {"alias": "Sales Channel",
             "expression": {"kind": "column", "table": "FactSales",
                            "column": "Channel"}},
        ],
        "group_by": [{"table": "FactSales", "column": "Channel"}],
        "filters": [], "order_by": [], "limit": None,
    }
    powerbi = FakePowerBI()
    agent, calls = make_agent([json.dumps(plan)], powerbi)
    agent.ask("Show gross profit by sales channel.")
    assert calls["chat"] == []
    assert len(powerbi.queries) == 1
    assert '"Sales Channel"' not in powerbi.queries[0]
    assert agent.last_trace["normalizations"][0]["kind"] == (
        "grouped_column_metric_dropped")


def test_measure_the_model_lacks_is_declined_before_planning():
    powerbi = FakePowerBI()
    agent, calls = make_agent([], powerbi)
    answer = agent.ask("Net profit over years.")
    assert calls["generate"] == 0 and powerbi.queries == []
    assert "no Net Profit measure" in answer
    assert "Gross Profit" in answer
    assert agent.last_trace["stages"][0] == "measure_name:missing"


def test_measure_name_check_can_be_switched_off():
    powerbi = FakePowerBI()
    agent, calls = make_agent([json.dumps(PLAN)], powerbi)
    agent.options["measure_name_check"] = False
    agent.ask("Net profit by region.")
    assert calls["generate"] == 1


@pytest.mark.parametrize("question,missing", [
    ("Net profit over years.", ["net profit"]),
    ("gross units by store", ["gross units"]),
    ("Net sales by year", []),
    ("Net revenue by month", []),
    ("Gross margin % by city", []),
    ("Gross profit by region", []),
    ("Sales net of discounts", []),
])
def test_unmatched_measure_phrases(question, missing):
    from evaluation.answerability import unmatched_measure_phrases
    assert [p for p, _ in unmatched_measure_phrases(question)] == missing
