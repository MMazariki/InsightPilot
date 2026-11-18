"""Every test uses its own users file and security log, never the real ones
in config\\ and logs\\. The file holds one administrator, ``tester``, with
every permission and every folder; page tests sign in as that user with
``sign_in(app)``."""

import json
import time

import pytest

from src.security import audit as security_audit
from src.security import users as security_users

TEST_PASSWORD = "correct-horse-42"
_HASH = security_users.hash_password(TEST_PASSWORD)  # once: scrypt is slow on purpose


def user_record(**changes):
    user = {"display_name": "Tester", "password": _HASH,
            "permissions": list(security_users.ALL_PERMISSIONS),
            "folders": [], "all_folders": True, "active": True,
            "must_change_password": False}
    user.update(changes)
    return user


@pytest.fixture(autouse=True)
def isolated_alerts(tmp_path, monkeypatch):
    from src import alerts
    from src.actions import audit as action_audit

    # The action log too: drafts made by any test never reach logs\actions.jsonl.
    monkeypatch.setattr(action_audit, "LOG", tmp_path / "actions_default.jsonl")

    monkeypatch.setattr(alerts, "ALERTS_FILE", tmp_path / "alerts.json")
    monkeypatch.setattr(alerts, "LOG", tmp_path / "alerts.jsonl")
    monkeypatch.setattr(alerts, "HEARTBEAT", tmp_path / "alerts_worker.json")


@pytest.fixture(autouse=True)
def isolated_users(tmp_path, monkeypatch):
    path = tmp_path / "users.json"
    data = security_users.empty()
    data["users"]["tester"] = user_record()
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(security_users, "USERS_FILE", path)
    monkeypatch.setattr(security_audit, "LOG", tmp_path / "security.jsonl")
    return path


def sign_in(app, username="tester"):
    app.session_state["auth_user"] = username
    app.session_state["auth_seen"] = time.time()
    return app
