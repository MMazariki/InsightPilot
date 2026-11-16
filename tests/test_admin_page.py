"""Admin settings wired into the agent and the Streamlit page."""

import json

import pytest

from src import agent_factory
from src import settings as app_settings
from src.structured_agent import StructuredPlanningAgent, format_result_table

PLAN = {
    "metrics": [{"alias": "Net Sales",
                 "expression": {"kind": "measure", "name": "Net Sales"}}],
    "group_by": [{"table": "DimStore", "column": "Region"}],
    "filters": [], "order_by": [], "limit": None,
}


class FakePowerBI:
    def __init__(self, rows=None):
        self.queries = []
        self.rows = rows if rows is not None else [
            {"DimStore[Region]": "Central", "[Net Sales]": 10.0}]

    def execute_dax(self, dax):
        self.queries.append(dax)
        columns = list(self.rows[0]) if self.rows else []
        return {"columns": columns, "rows": self.rows,
                "row_count": len(self.rows)}

    def close(self):
        pass


def make_agent(planner_replies, verifier_replies=(), options=None, powerbi=None):
    log = {"planner": [], "verifier": []}
    planner_replies = list(planner_replies)
    verifier_replies = list(verifier_replies)

    def generate(model, question, instructions):
        log["planner"].append(model)
        return planner_replies.pop(0)

    def chat(model, messages):
        log["planner"].append(model)
        return planner_replies.pop(0)

    def verifier_chat(model, messages):
        log["verifier"].append(model)
        return verifier_replies.pop(0)

    agent = StructuredPlanningAgent(
        model="planner-model", powerbi=powerbi or FakePowerBI(),
        generate=generate, chat=chat, options=options,
        verifier_chat=verifier_chat, verifier_model="verifier-model")
    return agent, log


# ---- Pipeline switches -----------------------------------------------

def test_verifier_uses_its_own_model():
    verdict = json.dumps({"faithful": True, "reason": "ok"})
    agent, log = make_agent([json.dumps(PLAN)], [verdict])
    agent.ask("Show me the totals.")  # names no metric -> verifier runs
    assert log["planner"] == ["planner-model"]
    assert log["verifier"] == ["verifier-model"]


def test_answerability_check_can_be_switched_off():
    powerbi = FakePowerBI()
    agent, log = make_agent([json.dumps(PLAN)], [],
                            options={"answerability_check": False},
                            powerbi=powerbi)
    agent.ask("Show me the totals.")
    assert log["verifier"] == []
    assert len(powerbi.queries) == 1


def test_no_metric_trigger_can_be_switched_off():
    powerbi = FakePowerBI()
    agent, log = make_agent([json.dumps(PLAN)], [],
                            options={"no_metric_trigger": False}, powerbi=powerbi)
    agent.ask("Show me the totals.")  # all words known, no metric named
    assert log["verifier"] == []
    assert len(powerbi.queries) == 1


def test_strict_metric_words_sends_grouping_only_questions_to_the_verifier():
    verdict = json.dumps({"faithful": False, "reason": "no metric named"})

    agent, log = make_agent([json.dumps(PLAN)], [verdict])
    agent.ask("Rank the regions.")
    assert log["verifier"] == []  # default: "regions" counts as a metric word

    powerbi = FakePowerBI()
    agent, log = make_agent([json.dumps(PLAN)], [verdict],
                            options={"strict_metric_words": True},
                            powerbi=powerbi)
    answer = agent.ask("Rank the regions.")
    assert log["verifier"] == ["verifier-model"]
    assert powerbi.queries == []
    assert "Which metric do you mean" in answer


def test_unusable_verifier_reply_fails_open_by_default_and_closed_on_request():
    powerbi = FakePowerBI()
    agent, _ = make_agent([json.dumps(PLAN)], ["not json"], powerbi=powerbi)
    agent.ask("Show me the totals.")
    assert len(powerbi.queries) == 1

    powerbi = FakePowerBI()
    agent, _ = make_agent([json.dumps(PLAN)], ["not json"],
                          options={"verifier_fail_closed": True},
                          powerbi=powerbi)
    answer = agent.ask("Show me the totals.")
    assert powerbi.queries == []
    assert "couldn't confirm" in answer
    assert "answerability:unverified" in agent.last_trace["stages"]


def test_repair_turn_can_be_switched_off():
    agent, log = make_agent(["{}", json.dumps(PLAN)])
    agent.ask("Net sales by region")
    assert log["planner"] == ["planner-model", "planner-model"]  # repaired

    powerbi = FakePowerBI()
    agent, log = make_agent(["{}"], options={"repair_turn": False},
                            powerbi=powerbi)
    answer = agent.ask("Net sales by region")
    assert log["planner"] == ["planner-model"]
    assert powerbi.queries == []
    assert "couldn't turn that question into a valid query" in answer


def test_request_guard_can_be_switched_off():
    agent, log = make_agent([])
    answer = agent.ask("Change the Q4 sales target to 5 million.")
    assert log["planner"] == [] and "No query was executed" in answer

    agent, log = make_agent(
        [json.dumps({"clarification": {
            "reason": "write_request", "message": "I can only read data."}})],
        options={"request_guard": False})
    answer = agent.ask("Change the Q4 sales target to 5 million.")
    assert log["planner"] == ["planner-model"]  # the model was consulted
    assert "I can only read data." in answer


def test_max_table_rows():
    rows = [{"A": index} for index in range(8)]
    table = format_result_table(["A"], rows, max_rows=3)
    assert "Showing the first 3 of 8 rows" in table
    assert table.count("\n| ") == 1 + 3  # divider + 3 body rows

    powerbi = FakePowerBI(rows=[
        {"DimStore[Region]": str(i), "[Net Sales]": 1.0} for i in range(8)])
    agent, _ = make_agent([json.dumps(PLAN)], options={"max_table_rows": 2},
                          powerbi=powerbi)
    assert "Showing the first 2 of 8 rows" in agent.ask("Net sales by region")


# ---- Factory -----------------------------------------------------------

def test_factory_builds_agent_kwargs_from_settings(monkeypatch):
    sent = []

    def fake_chat_once(config, messages, api_key=""):
        sent.append((config["provider"], config["model"], api_key))
        return json.dumps(PLAN) if len(sent) == 1 else json.dumps(
            {"faithful": True, "reason": "ok"})

    monkeypatch.setattr(agent_factory.llm, "chat_once", fake_chat_once)

    settings = app_settings.defaults()
    settings["planner"].update(provider="anthropic", model="claude-opus-5-5",
                               base_url="")
    settings["verifier"].update(same_as_planner=False,
                                provider="openai_compatible", model="gpt-x",
                                base_url="https://api.example.com/v1")
    settings["pipeline"]["max_table_rows"] = 7
    secrets = {"planner_api_key": "PKEY", "verifier_api_key": "VKEY"}

    kwargs = agent_factory.structured_agent_kwargs(
        settings, secrets=secrets, powerbi=FakePowerBI())
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["verifier_model"] == "gpt-x"
    assert kwargs["options"]["max_table_rows"] == 7

    agent = StructuredPlanningAgent(**kwargs)
    agent.ask("Show me the totals.")
    assert sent == [("anthropic", "claude-opus-5-5", "PKEY"),
                    ("openai_compatible", "gpt-x", "VKEY")]


def test_factory_passes_power_bi_settings(monkeypatch):
    captured = {}

    class Service:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import src.powerbi_query_service as service_module

    monkeypatch.setattr(service_module, "PowerBIQueryService", Service)
    settings = app_settings.defaults()
    settings["powerbi"].update(dataset_id="DS", timeout_seconds=90)
    agent_factory.structured_agent_kwargs(settings, secrets={})
    assert captured == {"dataset_id": "DS", "workspace_id": None,
                        "username": None, "timeout_seconds": 90}


# ---- The page ----------------------------------------------------------

@pytest.fixture
def app(tmp_path, monkeypatch):
    import importlib.util

    if importlib.util.find_spec("powerbi_embed_component") is None:
        pytest.skip("powerbi_embed_component is not installed")
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(app_settings, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(app_settings, "SECRETS_FILE", tmp_path / "secrets.bin")
    monkeypatch.setenv("INSIGHTPILOT_AGENT", "structured")
    from src import llm

    monkeypatch.setattr(llm, "list_ollama_models", lambda *a, **k: ["qwen2.5-coder:7b"])
    from pathlib import Path

    main = Path(__file__).resolve().parents[1] / "src" / "main.py"
    return AppTest.from_file(str(main), default_timeout=30)


def test_gear_button_opens_admin_and_back_returns(app):
    app.run()
    assert not app.exception
    assert app.session_state.view == "chat"
    app.button(key="open_admin").click().run()
    assert not app.exception
    assert app.session_state.view == "admin"
    assert [tab.label for tab in app.tabs] == [
        "Models", "Answer checks", "Power BI", "Interface", "Voice", "About"]
    assert app.button(key="adm_save").disabled  # nothing changed yet
    app.button(key="adm_back").click().run()
    assert app.session_state.view == "chat"


def test_saving_a_change_persists_and_applies_to_the_chat(app):
    app.session_state.view = "admin"
    app.run()
    app.text_input(key="adm_planner_model").set_value("qwen2.5-coder:3b")
    app.checkbox(key="adm_strict_metric_words").set_value(True)
    app.text_area(key="adm_starter_questions").set_value("Only question?")
    app.run()
    assert not app.exception
    assert not app.button(key="adm_save").disabled
    assert any("differ from the measured defaults" in i.value for i in app.info)

    app.button(key="adm_save").click().run()
    assert not app.exception
    saved = app_settings.load()
    assert saved["planner"]["model"] == "qwen2.5-coder:3b"
    assert saved["pipeline"]["strict_metric_words"] is True
    assert saved["interface"]["starter_questions"] == ["Only question?"]
    assert any("Settings saved" in s.value for s in app.success)

    app.button(key="adm_back").click().run()
    assert not app.exception
    labels = [b.label for b in app.button]
    assert "Only question?" in labels
    assert "Show gross profit by sales channel." not in labels


def test_invalid_value_blocks_saving(app):
    app.session_state.view = "admin"
    app.run()
    app.text_input(key="adm_planner_model").set_value("   ")
    app.run()
    assert any("model name is required" in e.value for e in app.error)
    assert app.button(key="adm_save").disabled
    assert not app_settings.SETTINGS_FILE.exists()


def test_cloud_provider_shows_warning_and_stores_key_encrypted(app):
    app.session_state.view = "admin"
    app.run()
    app.selectbox(key="adm_planner_provider").set_value("anthropic").run()
    assert not app.exception
    assert app.text_input(key="adm_planner_model").value == "claude-opus-5-5"
    assert any("cloud model is selected" in w.value for w in app.warning)

    app.text_input(key="adm_planner_api_key").set_value("sk-ant-test-0123456789")
    app.run()
    app.button(key="adm_save").click().run()
    assert not app.exception
    assert app_settings.load()["planner"]["provider"] == "anthropic"
    assert app_settings.load_secrets() == {
        "planner_api_key": "sk-ant-test-0123456789"}
    assert "sk-ant-test" not in app_settings.SETTINGS_FILE.read_text(encoding="utf-8")


def test_reset_restores_defaults(app):
    settings = app_settings.defaults()
    settings["pipeline"]["max_table_rows"] = 5
    app_settings.save(settings)
    app.session_state.view = "admin"
    app.run()
    assert app.number_input(key="adm_max_table_rows").value == 5
    app.button(key="adm_reset").click().run()
    assert not app.exception
    assert app_settings.load() == app_settings.DEFAULTS
    assert app.number_input(key="adm_max_table_rows").value == 50
