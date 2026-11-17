"""Executors: the only code that writes files or sends anything.

Each ``run_<kind>(draft, settings, ...)`` returns ``{"ok": bool, "detail": str}``
plus kind-specific fields. They are called only by ``service.approve`` after
the user clicked Approve on that exact draft. Failures are reported, never
retried silently.

* export      writes XLSX/CSV under the export folder (no network)
* teams_post  POSTs an Adaptive Card to the channel's Workflows webhook
* email       Microsoft Graph ``/me/sendMail`` as the signed-in user
* task        Microsoft Graph To Do tasks in the user's list
"""

import base64
import os
from pathlib import Path
from urllib.parse import urlparse

import requests

from src.actions import render

ROOT = Path(__file__).resolve().parents[2]
GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = {
    "email": ["https://graph.microsoft.com/Mail.Send"],
    "task": ["https://graph.microsoft.com/Tasks.ReadWrite"],
}
# Workflows ("Post to a channel when a webhook request is received") and the
# retiring Office 365 connector hosts.
WEBHOOK_HOSTS = (".logic.azure.com", ".api.powerplatform.com", ".webhook.office.com")
TIMEOUT = 30


class ActionError(Exception):
    pass


def webhook_url_problem(url):
    """Why ``url`` cannot be the Teams webhook, or "" if it can."""

    if not url:
        return "No Teams webhook address is saved (Admin -> Actions)."
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        return "The Teams webhook address must start with https://."
    if not any(host.endswith(h) for h in WEBHOOK_HOSTS):
        return ("The Teams webhook address is not a Teams Workflows address "
                "(…logic.azure.com or …api.powerplatform.com).")
    return ""


def export_folder(settings):
    folder = Path(settings["actions"]["export_folder"])
    return folder if folder.is_absolute() else ROOT / folder


# ------------------------------------------------------------ export

def run_export(draft, settings, **_):
    context = draft["context"]
    extension = draft.get("format", "xlsx")
    data = render.csv_bytes(context) if extension == "csv" else render.xlsx_bytes(context)
    folder = export_folder(settings)
    folder.mkdir(parents=True, exist_ok=True)
    name = Path(draft.get("filename") or render.file_name(context, extension)).name
    if not name.lower().endswith("." + extension):
        name += "." + extension
    path = folder / name
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)
    return {"ok": True, "detail": f"Saved {path.name} ({len(data):,} bytes) in "
            f"{folder}", "path": str(path), "bytes": len(data)}


# ------------------------------------------------------------ Teams

def run_teams_post(draft, settings, secrets=None, http=None, **_):
    url = (secrets or {}).get("teams_webhook_url", "")
    problem = webhook_url_problem(url)
    if problem:
        return {"ok": False, "detail": problem}
    card = render.adaptive_card(draft["context"], draft["subject"], draft.get("note", ""))
    http = http or requests
    try:
        response = http.post(url, json=render.teams_payload(card), timeout=TIMEOUT)
    except requests.RequestException as exc:
        return {"ok": False, "detail": f"Teams could not be reached: {type(exc).__name__}"}
    if response.status_code in (200, 201, 202):
        return {"ok": True, "detail": "Posted to the Teams channel"
                + (f" ({settings['actions']['teams_channel_label']})"
                   if settings["actions"].get("teams_channel_label") else "") + "."}
    return {"ok": False, "detail": f"Teams answered HTTP {response.status_code}: "
            f"{response.text[:200]}"}


# ------------------------------------------------------------ Graph

def graph_token(kind, settings, token_provider=None):
    """A delegated Graph token for ``kind``. ``token_provider`` replaces the
    Microsoft sign-in in tests."""

    if token_provider:
        return token_provider(SCOPES[kind])
    import msal
    from msal_extensions import PersistedTokenCache, build_encrypted_persistence

    tenant = os.environ.get("ENTRA_TENANT_ID")
    client = os.environ.get("ENTRA_CLIENT_ID")
    if not tenant or not client:
        raise ActionError("ENTRA_TENANT_ID and ENTRA_CLIENT_ID must be configured.")
    cache_file = ROOT / ".token_cache" / "msal_cache.bin"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    app = msal.PublicClientApplication(
        client_id=client, authority=f"https://login.microsoftonline.com/{tenant}",
        token_cache=PersistedTokenCache(build_encrypted_persistence(str(cache_file))))
    username = settings["powerbi"].get("username") or os.environ.get("ENTRA_USERNAME", "")
    accounts = app.get_accounts(username=username or None) or (
        [] if username else app.get_accounts())
    result = None
    if accounts:
        result = app.acquire_token_silent(SCOPES[kind], account=accounts[0])
    if not result or "access_token" not in result:
        # First use: one Microsoft consent window for the new permission.
        result = app.acquire_token_interactive(
            scopes=SCOPES[kind], login_hint=username or None, prompt="select_account",
            timeout=300)        # an unanswered window must not hang the app
    if "access_token" not in result:
        raise ActionError("Microsoft sign-in for this action failed: "
                          + str(result.get("error_description", result.get("error", "")))[:300])
    return result["access_token"]


def _graph_error(response):
    try:
        error = response.json().get("error", {})
        message = f"{error.get('code', '')}: {error.get('message', '')}".strip(": ")
    except ValueError:
        message = response.text[:200]
    if response.status_code == 403:
        message += (" (the app registration needs the Microsoft Graph permission "
                    "and admin consent; see docs/AGENT_ACTIONS_PLAN.md §6)")
    return f"Microsoft Graph answered HTTP {response.status_code}: {message}"


def run_email(draft, settings, token_provider=None, http=None, **_):
    try:
        token = graph_token("email", settings, token_provider)
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:300]}
    context = draft["context"]
    message = {
        "subject": draft["subject"],
        "body": {"contentType": "HTML", "content": render.email_html(context, draft.get("note", ""))},
        "toRecipients": [{"emailAddress": {"address": r["email"], "name": r["name"]}}
                         for r in draft["recipients"]],
    }
    if draft.get("attach") and context["rows"]:
        extension = draft.get("format", "xlsx")
        data = (render.csv_bytes(context) if extension == "csv"
                else render.xlsx_bytes(context))
        message["attachments"] = [{
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": render.file_name(context, extension),
            "contentBytes": base64.b64encode(data).decode("ascii")}]
    http = http or requests
    try:
        response = http.post(f"{GRAPH}/me/sendMail", json={"message": message,
                             "saveToSentItems": True},
                             headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return {"ok": False, "detail": f"Microsoft Graph could not be reached: "
                f"{type(exc).__name__}"}
    if response.status_code == 202:
        return {"ok": True, "detail": "Sent to " + ", ".join(
            r["email"] for r in draft["recipients"]) + " (a copy is in your Sent Items)."}
    return {"ok": False, "detail": _graph_error(response)}


def run_task(draft, settings, token_provider=None, http=None, **_):
    try:
        token = graph_token("task", settings, token_provider)
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:300]}
    http = http or requests
    headers = {"Authorization": f"Bearer {token}"}
    list_name = settings["actions"]["todo_list_name"]
    try:
        response = http.get(f"{GRAPH}/me/todo/lists", headers=headers, timeout=TIMEOUT)
        if response.status_code != 200:
            return {"ok": False, "detail": _graph_error(response)}
        lists = response.json().get("value", [])
        target = next((item for item in lists
                       if item.get("displayName", "").lower() == list_name.lower()), None)
        if target is None:
            response = http.post(f"{GRAPH}/me/todo/lists", headers=headers,
                                 json={"displayName": list_name}, timeout=TIMEOUT)
            if response.status_code != 201:
                return {"ok": False, "detail": _graph_error(response)}
            target = response.json()
        created = 0
        body = render.task_body(draft["context"])
        for task in draft["tasks"]:
            payload = {"title": task["title"],
                       "body": {"content": body, "contentType": "text"}}
            if task.get("due"):
                payload["dueDateTime"] = {"dateTime": f"{task['due']}T00:00:00",
                                          "timeZone": "UTC"}
            response = http.post(f"{GRAPH}/me/todo/lists/{target['id']}/tasks",
                                 headers=headers, json=payload, timeout=TIMEOUT)
            if response.status_code != 201:
                return {"ok": False, "created": created,
                        "detail": f"Created {created} of {len(draft['tasks'])} tasks; "
                        "then " + _graph_error(response)}
            created += 1
    except requests.RequestException as exc:
        return {"ok": False, "detail": f"Microsoft Graph could not be reached: "
                f"{type(exc).__name__}"}
    return {"ok": True, "created": created,
            "detail": f"Created {created} task{'s' if created != 1 else ''} in the "
            f"To Do list '{list_name}'."}


EXECUTORS = {"export": run_export, "teams_post": run_teams_post,
             "email": run_email, "task": run_task}
