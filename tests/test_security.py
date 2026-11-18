"""Sign-in, users, permissions and folder access (src/security, the Users
page, and their effect on the other pages)."""

import datetime as dt
import json
from pathlib import Path

import pytest

from src import settings as app_settings
from src.security import audit, users
from tests.conftest import TEST_PASSWORD, sign_in, user_record

GOOD = "Blue-river-2026"


def _data(**extra_users):
    data = users.empty()
    data["users"]["tester"] = users._normal_user(user_record())
    for name, record in extra_users.items():
        data["users"][name] = users._normal_user(record)
    return data


# ------------------------------------------------------------ passwords

def test_hash_is_salted_and_verifies():
    one, two = users.hash_password(GOOD), users.hash_password(GOOD)
    assert one != two and one.startswith("scrypt$") and GOOD not in one
    assert users.verify_password(GOOD, one)
    assert not users.verify_password("Blue-river-2025", one)
    assert not users.verify_password(GOOD, "garbage")
    assert not users.verify_password(GOOD, "")


@pytest.mark.parametrize("password,problem", [
    ("short1", "at least 10"),
    ("onlyletterslong", "digit or symbol"),
    ("1234567890123", "digit or symbol"),
    ("sara-secret-99", "username"),
    (GOOD, None),
])
def test_password_rules(password, problem):
    found = users.password_problem(password, "sara")
    assert (found is None) if problem is None else (problem in found)


# ------------------------------------------------------------ accounts

def test_first_admin_gets_everything_and_only_once():
    data = users.empty()
    name = users.create_first_admin(data, "  Admin ", GOOD, "Boss")
    user = data["users"][name]
    assert name == "admin" and user["permissions"] == list(users.ALL_PERMISSIONS)
    assert user["all_folders"] and not user["must_change_password"]
    with pytest.raises(users.AccessError):
        users.create_first_admin(data, "other", GOOD)


def test_create_user_checks_name_password_and_duplicates():
    data = _data()
    users.create_user(data, "sara", GOOD, "Sara", ["documents"], ["102"])
    assert data["users"]["sara"]["must_change_password"]
    with pytest.raises(users.AccessError, match="already taken"):
        users.create_user(data, "SARA", GOOD)
    with pytest.raises(users.AccessError, match="username"):
        users.create_user(data, "../x", GOOD)
    with pytest.raises(users.AccessError, match="at least"):
        users.create_user(data, "omar", "short")
    with pytest.raises(users.AccessError, match="Unknown permission"):
        users.create_user(data, "omar", GOOD, permissions=["root"])
    with pytest.raises(users.AccessError, match="folder"):
        users.create_user(data, "omar", GOOD, folders=["..\\secret"])


def test_sign_in_messages_lockout_and_deactivation():
    now = dt.datetime(2026, 11, 19, 10, 0)
    data = _data()
    assert users.authenticate(data, "nobody", GOOD, now)[2] == "Wrong username or password."
    assert users.authenticate(data, "tester", "wrong", now)[2] == "Wrong username or password."
    name, event, _ = users.authenticate(data, " TESTER ", TEST_PASSWORD, now)
    assert (name, event) == ("tester", "signed_in")
    for _ in range(4):
        users.authenticate(data, "tester", "wrong", now)
    name, event, message = users.authenticate(data, "tester", "wrong", now)
    assert (name, event) == (None, "locked") and "15 minutes" in message
    # Locked: even the right password is refused until the time is over.
    assert users.authenticate(data, "tester", TEST_PASSWORD, now)[0] is None
    later = now + dt.timedelta(minutes=16)
    assert users.authenticate(data, "tester", TEST_PASSWORD, later)[0] == "tester"
    data["users"]["tester"]["active"] = False
    assert "deactivated" in users.authenticate(data, "tester", TEST_PASSWORD, later)[2]


def test_admin_cannot_lock_out_themselves_or_the_last_manager():
    data = _data(bob=user_record(permissions=["power_bi"]))
    with pytest.raises(users.AccessError, match="your own"):
        users.update_user(data, "tester", "tester", permissions=["power_bi"])
    with pytest.raises(users.AccessError, match="your own"):
        users.update_user(data, "tester", "tester", active=False)
    with pytest.raises(users.AccessError, match="your own"):
        users.delete_user(data, "tester", "tester")
    # Someone else removing the only manager: refused, nothing changed.
    with pytest.raises(users.AccessError, match="At least one"):
        users.update_user(data, "tester", "bob", active=False)
    with pytest.raises(users.AccessError, match="At least one"):
        users.delete_user(data, "tester", "bob")
    assert data["users"]["tester"]["active"]
    users.delete_user(data, "bob", "tester")
    assert set(data["users"]) == {"tester"}


def test_password_change_and_reset():
    data = _data()
    with pytest.raises(users.AccessError, match="different"):
        users.set_password(data, "tester", TEST_PASSWORD)
    users.set_password(data, "tester", GOOD, must_change=True)
    assert data["users"]["tester"]["must_change_password"]
    assert users.authenticate(data, "tester", GOOD)[0] == "tester"


def test_damaged_users_file_refuses_to_run(tmp_path):
    path = tmp_path / "users.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="cannot be read"):
        users.load(path)
    assert users.load(tmp_path / "missing.json")["users"] == {}


# ------------------------------------------------------- folder access

def test_folder_access_needs_the_permission_and_the_assignment():
    sara = users._normal_user(user_record(permissions=["documents"], folders=["102"],
                                            all_folders=False))
    assert users.folder_allowed(sara, "102") and not users.folder_allowed(sara, "101")
    assert users.allowed_folders(sara, ["101", "102", "103"]) == ["102"]
    no_docs = {**sara, "permissions": ["power_bi"]}
    assert not users.folder_allowed(no_docs, "102") and users.allowed_folders(no_docs, ["102"]) == []
    every = {**sara, "all_folders": True}
    assert users.allowed_folders(every, ["9", "10", "abc"]) == ["9", "10", "abc"]
    inactive = {**sara, "active": False}
    assert not users.folder_allowed(inactive, "102")


def test_set_folder_access_adds_and_removes():
    data = _data(sara=user_record(folders=["102"], all_folders=False),
                 omar=user_record(folders=[], all_folders=False))
    added, removed = users.set_folder_access(data, "102", ["omar"])
    assert (added, removed) == (["omar"], ["sara"])
    assert data["users"]["omar"]["folders"] == ["102"] and data["users"]["sara"]["folders"] == []
    with pytest.raises(users.AccessError):
        users.set_folder_access(data, "102", ["ghost"])
    with pytest.raises(users.AccessError):
        users.set_folder_access(data, "../x", [])


# ------------------------------------------------------------- the pages

@pytest.fixture
def make_app(tmp_path, monkeypatch):
    import importlib.util

    if importlib.util.find_spec("powerbi_embed_component") is None:
        pytest.skip("powerbi_embed_component is not installed")
    from streamlit.testing.v1 import AppTest

    from src import documents_page, llm, report_pages
    from src.rag import service as rag_service

    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(app_settings, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(app_settings, "SECRETS_FILE", tmp_path / "secrets.bin")
    monkeypatch.setenv("INSIGHTPILOT_AGENT", "structured")
    monkeypatch.setattr(llm, "list_ollama_models", lambda *a, **k: ["qwen2.5-coder:7b"])
    monkeypatch.setattr(report_pages, "resolve_page_id", lambda *a, **k: None)
    monkeypatch.setattr(report_pages, "query_account", lambda *a, **k: "me@company.com")
    # Documents: a folder of our own and an index of our own, never the real ones.
    monkeypatch.setattr(documents_page, "DocumentService",
                        lambda config: rag_service.DocumentService(
                            config, index_dir=tmp_path / "index"))
    docs = tmp_path / "documents"
    for folder in ("101", "102", "103"):
        (docs / folder).mkdir(parents=True)
    settings = app_settings.defaults()
    settings["documents"]["folder"] = str(docs)
    settings["documents"]["auto_index"] = False
    settings_file.write_text(json.dumps(settings), encoding="utf-8")

    def make(user="tester", **records):
        if records:
            data = users.load()
            for name, record in records.items():
                data["users"][name] = users._normal_user(record)
            users.save(data)
        main = Path(__file__).resolve().parents[1] / "src" / "main.py"
        app = AppTest.from_file(str(main), default_timeout=30)
        if user:
            sign_in(app, user)
        return app

    make.docs = docs
    return make


def test_first_run_creates_the_admin_and_signs_in(make_app):
    users.USERS_FILE.unlink()
    app = make_app(user=None)
    app.run()
    assert not app.exception
    assert app.text_input(key="auth_setup_username")
    app.text_input(key="auth_setup_username").set_value("Admin")
    app.text_input(key="auth_setup_pw").set_value(GOOD)
    app.text_input(key="auth_setup_pw2").set_value(GOOD)
    app.button[0].click().run()
    assert not app.exception
    assert app.session_state.auth_user == "admin"
    assert app.session_state.view == "chat"
    stored = users.USERS_FILE.read_text(encoding="utf-8")
    assert GOOD not in stored and json.loads(stored)["users"]["admin"]["all_folders"]
    assert [e["event"] for e in audit.entries()] == ["setup"]


def test_sign_in_screen_blocks_until_the_right_password(make_app):
    app = make_app(user=None)
    app.run()
    assert not [b for b in app.button if b.key == "open_admin"]
    app.text_input(key="auth_username").set_value("tester")
    app.text_input(key="auth_password").set_value("wrong-password-1")
    app.button[0].click().run()
    assert any("Wrong username or password" in e.value for e in app.error)
    assert "auth_user" not in app.session_state
    app.text_input(key="auth_password").set_value(TEST_PASSWORD)
    app.button[0].click().run()
    assert not app.exception
    assert app.session_state.auth_user == "tester"
    assert [b for b in app.button if b.key == "open_admin"]
    events = [e["event"] for e in audit.entries()]
    assert events == ["sign_in_failed", "signed_in"]
    assert TEST_PASSWORD not in json.dumps(audit.entries())


def _ribbon(app):
    return " ".join(h.proto.body for h in app.get("html")
                    if '<div class="ip-brand">' in h.proto.body)


def test_ribbon_shows_the_signed_in_user(make_app):
    app = make_app(user=None)
    app.run()
    assert "ip-user" not in _ribbon(app)             # sign-in screen: nobody yet
    app = make_app("ana", ana=user_record(display_name="Ana <b>", permissions=["power_bi"]))
    app.run()
    assert not app.exception
    ribbon = _ribbon(app)
    assert ribbon.count("ip-user\"") == 1
    assert "Ana &lt;b&gt;" in ribbon and "· ana" in ribbon   # escaped, with the username


def test_admin_page_follows_the_permission(make_app):
    analyst = user_record(permissions=["power_bi"], all_folders=False)
    app = make_app("ana", ana=analyst)
    app.session_state.view = "admin"            # e.g. from ?view=admin
    app.run()
    assert not app.exception
    assert app.session_state.view == "chat"
    keys = {b.key for b in app.button}
    assert not keys & {"open_admin", "open_access", "open_evaluation", "open_documents"}
    # Given the parameter, the same user gets the Admin page.
    data = users.load()
    users.update_user(data, "ana", "tester", permissions=["power_bi", "admin_settings"])
    users.save(data)
    app.run()
    app.button(key="open_admin").click().run()
    assert not app.exception and app.session_state.view == "admin"


def test_documents_user_sees_only_assigned_folders(make_app):
    reader = user_record(permissions=["documents"], folders=["102", "103"],
                           all_folders=False)
    app = make_app("rita", rita=reader)
    app.run()
    assert not app.exception
    assert app.session_state.view == "documents"      # no Power BI permission
    box = app.selectbox(key="doc_folder_input")
    assert list(box.options) == ["102", "103"]
    assert not [b for b in app.button if b.key == "doc_back"]
    # Indexing works on every folder: only for the Admin page permission.
    assert not {b.key for b in app.button} & {"doc_index", "doc_rebuild"}


def test_index_buttons_for_admins(make_app):
    app = make_app()
    app.session_state.view = "documents"
    app.run()
    assert not app.exception
    assert {"doc_index", "doc_rebuild"} <= {b.key for b in app.button}


def test_documents_link_to_another_folder_is_ignored(make_app):
    reader = user_record(permissions=["documents"], folders=["102"], all_folders=False)
    app = make_app("rita", rita=reader)
    app.query_params["view"] = "documents"
    app.query_params["folder"] = "101"
    app.run()
    assert not app.exception
    # The only assigned folder is chosen; folder 101 from the link is not.
    assert app.selectbox(key="doc_folder_input").value == "102"


def test_no_permission_at_all_shows_a_notice(make_app):
    app = make_app("nobody", nobody=user_record(permissions=[], all_folders=False))
    app.run()
    assert not app.exception
    assert any("no pages assigned" in w.value for w in app.warning)


def test_forced_password_change(make_app):
    app = make_app("newbie", newbie=user_record(must_change_password=True))
    app.run()
    assert app.text_input(key="auth_forced_current")
    app.text_input(key="auth_forced_current").set_value(TEST_PASSWORD)
    app.text_input(key="auth_forced_new").set_value(GOOD)
    app.text_input(key="auth_forced_confirm").set_value(GOOD)
    app.button[0].click().run()
    assert not app.exception
    user = users.load()["users"]["newbie"]
    assert not user["must_change_password"] and users.verify_password(GOOD, user["password"])


def test_deactivated_user_is_signed_out_on_the_next_click(make_app):
    app = make_app("ana", ana=user_record(permissions=["power_bi"]))
    app.run()
    data = users.load()
    users.update_user(data, "ana", "tester", active=False)
    users.save(data)
    app.run()
    assert not app.exception
    assert "auth_user" not in app.session_state
    assert app.text_input(key="auth_username")


def test_access_page_creates_a_user_and_assigns_a_folder(make_app):
    app = make_app()
    app.run()
    app.button(key="open_access").click().run()
    assert not app.exception and app.session_state.view == "access"
    assert [t.label for t in app.tabs] == ["Users", "Document folders",
                                          "Sign-in policy", "Security log"]
    app.text_input(key="acc_new_username").set_value("Sara")
    app.text_input(key="acc_new_display").set_value("Sara Ali")
    app.multiselect(key="acc_new_folders").set_value(["101"])
    next(b for b in app.button if b.label == "Create user").click().run()
    assert not app.exception
    sara = users.load()["users"]["sara"]
    assert sara["folders"] == ["101"] and sara["must_change_password"]
    assert sara["permissions"] == ["power_bi", "documents"]
    assert any("Temporary password" in s.value for s in app.success)

    # Grant folder 103 from the folder side.
    data = users.load()
    users.set_folder_access(data, "103", ["sara"])
    users.save(data)
    assert users.folder_allowed(users.load()["users"]["sara"], "103")
    events = [e["event"] for e in audit.entries()]
    assert "user_created" in events


def _open_user(app, name):
    app.run()
    app.button(key="open_access").click().run()
    app.selectbox(key="acc_edit_user").set_value(name).run()
    assert not app.exception
    return f"acc_edit_{name}"


def test_admin_changes_a_users_password(make_app):
    app = make_app(sara=user_record(permissions=["documents"]))
    prefix = _open_user(app, "sara")
    app.text_input(key=f"{prefix}_setpw_new").set_value(GOOD)
    app.text_input(key=f"{prefix}_setpw_repeat").set_value("Different-2026")
    next(b for b in app.button if b.label == "Set password").click().run()
    assert any("differ" in e.value for e in app.error)
    assert users.verify_password(TEST_PASSWORD, users.load()["users"]["sara"]["password"])

    app.text_input(key=f"{prefix}_setpw_repeat").set_value(GOOD)
    app.checkbox(key=f"{prefix}_setpw_must").uncheck()
    next(b for b in app.button if b.label == "Set password").click().run()
    assert not app.exception
    sara = users.load()["users"]["sara"]
    assert users.verify_password(GOOD, sara["password"]) and not sara["must_change_password"]
    assert any("Password of **sara** changed" in s.value for s in app.success)
    entry = audit.entries()[-1]
    assert entry["event"] == "password_reset" and entry["by"] == "tester"
    assert GOOD not in json.dumps(audit.entries())


def test_admin_deletes_a_user_but_not_themselves(make_app):
    app = make_app(sara=user_record(permissions=["documents"]))
    prefix = _open_user(app, "sara")
    app.button(key=f"{prefix}_delete").click().run()
    assert not app.exception
    assert "sara" not in users.load()["users"]
    assert any("deleted" in s.value for s in app.success)
    # Your own account: the delete and password buttons are not offered.
    app.selectbox(key="acc_edit_user").set_value("tester").run()
    assert app.button(key="acc_edit_tester_reset").disabled


def test_action_needs_the_actions_permission(make_app):
    from tests.test_action_flow import FakeAgent

    app = make_app("ana", ana=user_record(permissions=["power_bi"]))
    app.run()
    app.session_state.agent = FakeAgent()
    app.chat_input(key="agent_chat_input").set_value("Net sales by region").run()
    app.chat_input(key="agent_chat_input").set_value("save this as excel").run()
    assert not app.exception
    assert app.session_state.action_drafts == {}
    assert "not allowed to run actions" in app.session_state.messages[-1]["content"]
    assert not [b for b in app.button if b.key and b.key.startswith("quick_")]
