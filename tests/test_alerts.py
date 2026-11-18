"""Alerts (src/alerts.py, src/alerts_page.py): validation, schedule, the
check and its email, and the page. No real model, Power BI or email."""

import datetime as dt
import json
from pathlib import Path

import pytest

from src import alerts
from src import settings as app_settings
from src.actions import audit
from tests.conftest import sign_in, user_record

ROWS = [{"DimStore[StoreName]": "Riyadh Mall", "[Target Achievement %]": 0.92},
        {"DimStore[StoreName]": "Jeddah Corniche", "[Target Achievement %]": 0.71},
        {"DimStore[StoreName]": "Dammam Mall", "[Target Achievement %]": 0.64}]
BOOK = [{"name": "Sara Ali", "email": "sara@company.com", "role": "", "aliases": []}]
WED_9 = dt.datetime(2026, 11, 18, 9, 0)         # a Wednesday


class FakeAgent:
    def __init__(self, rows=ROWS, fail=None):
        self.rows, self.fail = rows, fail
        self.last_result, self.last_trace, self.closed = None, {}, False

    def ask(self, question):
        if self.fail:
            raise RuntimeError(self.fail)
        if self.rows is None:
            self.last_result = {"kind": "decline"}
            return "I cannot answer that."
        self.last_result = {"kind": "answer", "rows": self.rows, "dax": "EVALUATE ...",
                            "columns": ["DimStore[StoreName]", "[Target Achievement %]"]}
        return "table"

    def close(self):
        self.closed = True


def _settings(enabled=True):
    s = app_settings.defaults()
    s["actions"].update({"enabled": enabled, "address_book": BOOK,
                         "allowed_domains": ["company.com"], "email_via": "smtp",
                         "smtp_username": "me@company.com"})
    return s


def _alert(**changes):
    alert = {"id": "a1", "name": "Below target", "question": "Target achievement % by store",
             "condition": "below 80%", "time": "08:00", "days": [0, 1, 2, 3, 6],
             "recipients": ["me", "sara@company.com"], "attach": True, "enabled": True}
    alert.update(changes)
    return alert


SECRETS = {"smtp_password": "app"}


# ------------------------------------------------------------- validation

def test_clean_checks_every_field():
    ok = alerts.clean(_alert(name="  Below  ", time="07:30"), BOOK, ["company.com"],
                      "me@company.com")
    assert ok["name"] == "Below" and ok["time"] == "07:30"
    for change, message in [({"name": ""}, "name"), ({"question": ""}, "question"),
                            ({"time": "25:00"}, "HH:MM"), ({"days": []}, "day"),
                            ({"recipients": []}, "recipient"),
                            ({"recipients": ["x@evil.com"]}, "address book"),
                            ({"condition": "when it rains"}, "below 80%")]:
        with pytest.raises(alerts.AlertError, match=message):
            alerts.clean(_alert(**change), BOOK, ["company.com"], "me@company.com")
    # "me" needs a known own address.
    with pytest.raises(alerts.AlertError):
        alerts.clean(_alert(recipients=["me"]), BOOK, [], "")


@pytest.mark.parametrize("question,condition,expected", [
    ("Target Achievement below 80%", "", ("Target Achievement", "below 80%")),
    ("Target achievement % by store below 85%", "",
     ("Target achievement % by store", "below 85%")),
    ("Stores with net sales above 100000 this month", "",
     ("Stores with net sales this month", "above 100000")),
    ("Net sales by region", "", ("Net sales by region", "")),
    ("Target Achievement below 80%", "at most 50%", ("Target Achievement below 80%",
                                                      "at most 50%")),
])
def test_condition_written_in_the_question_is_moved(question, condition, expected):
    assert alerts.split_condition(question, condition) == expected


def test_saved_alert_gets_the_condition_from_the_question():
    ok = alerts.clean(_alert(question="Target achievement % by store below 80%",
                             condition=""), BOOK, ["company.com"], "me@company.com")
    assert (ok["question"], ok["condition"]) == ("Target achievement % by store", "below 80%")


def test_schedule():
    alert = _alert()
    assert alerts.due(alert, WED_9)
    assert not alerts.due(alert, WED_9.replace(hour=7, minute=59))
    assert not alerts.due({**alert, "last_run": "2026-11-18"}, WED_9)
    assert not alerts.due({**alert, "enabled": False}, WED_9)
    friday = dt.datetime(2026, 11, 20, 9, 0)
    assert not alerts.due(alert, friday)                # Friday not chosen
    assert alerts.next_run({**alert, "last_run": "2026-11-18"}, WED_9) == \
        dt.datetime(2026, 11, 19, 8, 0)
    assert alerts.next_run(alert, friday) == dt.datetime(2026, 11, 22, 8, 0)  # Sunday


# ------------------------------------------------------------------ check

def _run(tmp_path, alert, agent=None, settings=None, sent=None):
    sent = [] if sent is None else sent

    def send(draft, settings, secrets=None):
        sent.append(draft)
        return {"ok": True, "detail": "Sent."}

    entry = alerts.run(alert, settings or _settings(), SECRETS, "me@company.com", by="test",
                       agent_factory=lambda s: agent or FakeAgent(), send=send, now=WED_9,
                       action_log=tmp_path / "actions.jsonl")
    return entry, sent


def test_matching_rows_are_emailed(tmp_path):
    entry, sent = _run(tmp_path, _alert())
    assert entry["status"] == "sent" and entry["rows"] == 2
    draft = sent[0]
    assert [r["email"] for r in draft["recipients"]] == ["me@company.com", "sara@company.com"]
    assert [r["DimStore[StoreName]"] for r in draft["context"]["rows"]] == [
        "Jeddah Corniche", "Dammam Mall"]
    assert draft["subject"] == "InsightPilot alert: Below target"
    assert "2 rows where Target Achievement % < 80%" in draft["note"]
    assert draft["attach"]
    # Counted with the chat emails (shared daily limit).
    logged = audit.entries(tmp_path / "actions.jsonl")
    assert [e["event"] for e in logged] == ["sent"] and logged[0]["kind"] == "email"
    assert alerts.entries()[-1]["status"] == "sent"


def test_no_match_sends_nothing(tmp_path):
    entry, sent = _run(tmp_path, _alert(condition="below 50%"))
    assert entry["status"] == "no_match" and sent == []


def test_no_condition_is_a_report(tmp_path):
    entry, sent = _run(tmp_path, _alert(condition=""))
    assert entry["status"] == "sent" and len(sent[0]["context"]["rows"]) == 3
    assert sent[0]["subject"].startswith("InsightPilot report:")


def test_old_alert_with_condition_in_question_and_empty_answers(tmp_path):
    asked = []

    class Recording(FakeAgent):
        def ask(self, question):
            asked.append(question)
            return super().ask(question)

    entry, sent = _run(tmp_path, _alert(question="Target achievement % by store below 80%",
                                        condition=""), agent=Recording())
    assert asked == ["Target achievement % by store"]
    assert entry["status"] == "sent" and entry["rows"] == 2
    # A report whose answer has no rows sends nothing.
    entry, sent = _run(tmp_path, _alert(condition=""), agent=FakeAgent(rows=[]))
    assert entry["status"] == "no_match" and sent == []


def test_failures_are_logged_not_sent(tmp_path):
    entry, sent = _run(tmp_path, _alert(), agent=FakeAgent(fail="Power BI is down"))
    assert entry["status"] == "error" and "Power BI is down" in entry["detail"] and not sent
    entry, sent = _run(tmp_path, _alert(), agent=FakeAgent(rows=None))
    assert entry["status"] == "error" and "no data answer" in entry["detail"]
    entry, sent = _run(tmp_path, _alert(), settings=_settings(enabled=False))
    assert entry["status"] == "not_sent" and "switched off" in entry["detail"] and not sent
    # A recipient removed from the address book since: refused.
    entry, sent = _run(tmp_path, _alert(recipients=["omar@other.com"]))
    assert entry["status"] == "not_sent" and not sent


def test_run_due_runs_once_a_day(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(app_settings, "SECRETS_FILE", tmp_path / "secrets.bin")
    app_settings.save(_settings())
    monkeypatch.setattr(app_settings, "load_secrets", lambda: SECRETS)
    alerts.save([_alert(), _alert(id="a2", name="Off", enabled=False)])
    sent = []
    kwargs = {"agent_factory": lambda s: FakeAgent(),
              "send": lambda d, s, secrets=None: sent.append(d) or {"ok": True, "detail": ""},
              "action_log": tmp_path / "actions.jsonl"}
    assert [e["status"] for e in alerts.run_due(WED_9, **kwargs)] == ["sent"]
    assert alerts.run_due(WED_9.replace(minute=30), **kwargs) == []
    stored = {a["id"]: a for a in alerts.load()}
    assert stored["a1"]["last_run"] == "2026-11-18" and stored["a1"]["last_status"] == "sent"
    assert "last_run" not in stored["a2"]
    assert len(sent) == 1


def test_worker_loop_writes_a_heartbeat(monkeypatch):
    calls = []
    monkeypatch.setattr(alerts, "run_due", lambda: calls.append(1))
    assert not alerts.worker_status()["running"]
    alerts.work(interval=0, ticks=2)
    assert len(calls) == 2
    status = alerts.worker_status()
    assert status["running"] and status["last_error"] == ""
    later = dt.datetime.now() + dt.timedelta(minutes=5)
    assert not alerts.worker_status(later)["running"]


def test_worker_survives_an_error_and_stops_on_the_flag(monkeypatch, tmp_path):
    def boom():
        raise RuntimeError("Ollama is down")
    monkeypatch.setattr(alerts, "run_due", boom)
    alerts.work(interval=0, ticks=1)
    assert alerts.worker_status()["last_error"] == "Ollama is down"
    flag = tmp_path / "stop.flag"
    flag.write_text("stop")
    alerts.work(interval=0, stop_flag=flag)          # returns at once


# ------------------------------------------------------------------- page

@pytest.fixture
def make_app(tmp_path, monkeypatch):
    import importlib.util

    if importlib.util.find_spec("powerbi_embed_component") is None:
        pytest.skip("powerbi_embed_component is not installed")
    from streamlit.testing.v1 import AppTest

    from src import llm, report_pages

    monkeypatch.setattr(app_settings, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(app_settings, "SECRETS_FILE", tmp_path / "secrets.bin")
    monkeypatch.setenv("INSIGHTPILOT_AGENT", "structured")
    monkeypatch.setattr(llm, "list_ollama_models", lambda *a, **k: ["qwen2.5-coder:7b"])
    monkeypatch.setattr(report_pages, "resolve_page_id", lambda *a, **k: None)
    monkeypatch.setattr(report_pages, "query_account", lambda *a, **k: "me@company.com")
    app_settings.save(_settings())

    def make(user="tester"):
        main = Path(__file__).resolve().parents[1] / "src" / "main.py"
        return sign_in(AppTest.from_file(str(main), default_timeout=30), user)

    return make


def test_bell_opens_alerts_and_a_new_alert_is_saved(make_app):
    app = make_app()
    app.run()
    app.button(key="open_alerts").click().run()
    assert not app.exception and app.session_state.view == "alerts"
    assert any("not running" in w.value for w in app.warning)     # no worker in tests
    app.text_input(key="alert_new_name").set_value("Below target")
    app.text_input(key="alert_new_question").set_value("Target achievement % by store")
    app.text_input(key="alert_new_condition").set_value("below 80%")
    app.multiselect(key="alert_new_recipients").set_value(["me", "sara@company.com"])
    next(b for b in app.button if b.label == "Save alert").click().run()
    assert not app.exception
    stored = alerts.load()
    assert len(stored) == 1 and stored[0]["created_by"] == "tester"
    assert stored[0]["recipients"] == ["me", "sara@company.com"]
    assert any("created" in s.value for s in app.success)


def test_bad_alert_is_not_saved(make_app):
    app = make_app()
    app.session_state.view = "alerts"
    app.run()
    app.text_input(key="alert_new_name").set_value("X")
    app.text_input(key="alert_new_question").set_value("Net sales by store")
    app.text_input(key="alert_new_condition").set_value("when it rains")
    next(b for b in app.button if b.label == "Save alert").click().run()
    assert any("below 80%" in e.value for e in app.error)
    assert alerts.load() == []


def test_alerts_need_the_admin_permission(make_app):
    from src.security import users

    data = users.load()
    data["users"]["ana"] = users._normal_user(user_record(permissions=["power_bi", "actions"]))
    users.save(data)
    app = make_app("ana")
    app.session_state.view = "alerts"
    app.run()
    assert not app.exception and app.session_state.view == "chat"
    assert not [b for b in app.button if b.key == "open_alerts"]
