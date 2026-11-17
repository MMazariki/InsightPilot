"""The life of an action draft: create, show, edit, approve once, or cancel.

Rules enforced here (docs/AGENT_ACTIONS_PLAN.md §4.2):

* A draft is created only from the user's own chat message (``origin``) and
  only about an answer the app already checked (``context``).
* ``approve`` runs the executor only if the draft is still pending, was shown
  to the user on an approval card (``shown``), has not expired, and passes
  every check again after the user's edits. It runs at most once: the status
  leaves "pending" before the executor starts, so a double click does nothing.
* Every step is written to the audit log.
"""

import copy
import time
import uuid

from src.actions import address_book, audit, render
from src.actions.executors import EXECUTORS
from src.actions.validate import ORIGIN, problems

KIND_LABELS = {"email": "Email", "teams_post": "Teams post", "task": "To Do tasks",
               "export": "Export"}


def context_from(question, last_result, notes=None, answered_at=""):
    """The answer an action is about, or ``None`` if there was no data answer."""

    if not last_result or last_result.get("kind") != "answer":
        return None
    return {"question": question, "columns": list(last_result["columns"]),
            "rows": copy.deepcopy(last_result["rows"]), "notes": list(notes or []),
            "answered_at": answered_at}


def create_draft(parsed, context, request, settings, self_address="", now=None, log=None):
    now = now or time.time()
    actions = settings["actions"]
    draft = {
        "id": uuid.uuid4().hex,
        "kind": parsed["kind"],
        "origin": ORIGIN,
        "request": request,
        "status": "pending",
        "shown": False,
        "created_ts": now,
        "expires_ts": now + 60 * actions["draft_expiry_minutes"],
        "context": copy.deepcopy(context),
        "note": parsed.get("note", ""),
        "subject": render.subject(context),
        "recipients": [],
        "unresolved": [],
        "tasks": [],
        "format": parsed.get("format", "xlsx"),
    }
    kind = parsed["kind"]
    if kind == "email":
        draft["recipients"], draft["unresolved"] = address_book.resolve(
            parsed.get("recipients", []), actions["address_book"],
            actions["allowed_domains"], self_address)
        draft["attach"] = bool(actions["attach_xlsx"]) and bool(context["rows"])
    elif kind == "task":
        rows, described, problem = render.filter_rows(context, parsed.get("row_filter"))
        per_row = parsed.get("per_row") or bool(parsed.get("row_filter"))
        due = parsed.get("due")
        draft["filter"] = described
        draft["filter_problem"] = problem
        draft["tasks"] = [] if problem else [
            {"title": title, "due": due.isoformat() if due else ""}
            for title in render.task_titles(context, rows, parsed.get("task_text", ""),
                                            per_row)]
        draft["subject"] = ""
    elif kind == "export":
        draft["filename"] = render.file_name(context, draft["format"])
        draft["subject"] = ""
    audit.record("drafted", draft, log)
    return draft


def apply_edits(draft, edits, settings, self_address=""):
    """Apply the card's fields. Only presentational fields can change: never
    the kind, origin or the answer itself."""

    if draft["status"] != "pending":
        return draft
    actions = settings["actions"]
    if "subject" in edits:
        draft["subject"] = (edits["subject"] or "").strip()
    if "note" in edits:
        draft["note"] = (edits["note"] or "").strip()
    if "attach" in edits:
        draft["attach"] = bool(edits["attach"])
    if edits.get("format") in ("xlsx", "csv"):
        draft["format"] = edits["format"]
        if draft["kind"] == "export":
            stem = draft.get("filename", "answer.xlsx").rsplit(".", 1)[0]
            draft["filename"] = f"{stem}.{draft['format']}"
    if "recipients" in edits:
        book = {e["email"]: e["name"] for e in actions["address_book"]}
        if self_address:
            book.setdefault(self_address.lower(), "Me")
        chosen = [{"name": book.get(email, email), "email": email}
                  for email in edits["recipients"]]
        typed = [p.strip() for p in (edits.get("extra") or "").replace(";", ",").split(",")
                 if p.strip()]
        added, unresolved = address_book.resolve(
            typed, actions["address_book"], actions["allowed_domains"], self_address)
        seen = set()
        draft["recipients"] = [r for r in chosen + added
                               if not (r["email"] in seen or seen.add(r["email"]))]
        draft["unresolved"] = unresolved
    if "tasks" in edits:
        due = edits.get("due", "")
        draft["tasks"] = [{"title": line.strip()[:250], "due": due or ""}
                          for line in edits["tasks"].splitlines() if line.strip()]
    return draft


def expired(draft, now=None):
    return (now or time.time()) > draft["expires_ts"]


def close(draft, event, log=None, **detail):
    """Cancel, expire or supersede a pending draft."""

    if draft and draft["status"] == "pending":
        draft["status"] = event
        audit.record(event, draft, log, **detail)


def approve(draft, settings, secrets=None, self_address="", now=None, log=None,
            executors=None, **executor_kwargs):
    """Run the action once. Returns ``{"ok", "detail", ...}``; on refused
    checks ``{"ok": False, "problems": [...]}`` and the draft stays editable."""

    if draft["status"] != "pending":
        return {"ok": False, "ignored": True, "detail": "This draft was already handled."}
    if not draft.get("shown"):
        return {"ok": False, "ignored": True,
                "detail": "The draft must be shown on its card before approval."}
    if expired(draft, now):
        close(draft, "expired", log)
        return {"ok": False, "detail": "This draft expired; ask again to make a new one."}
    found = problems(draft, settings, secrets, self_address, log)
    if found:
        audit.record("refused", draft, log, problems=found)
        return {"ok": False, "problems": found, "detail": " ".join(found)}
    draft["status"] = "running"
    audit.record("approved", draft, log)
    executor = (executors or EXECUTORS)[draft["kind"]]
    try:
        result = executor(draft, settings, secrets=secrets, **executor_kwargs)
    except Exception as exc:          # reported, never retried
        result = {"ok": False, "detail": f"{type(exc).__name__}: {str(exc)[:300]}"}
    draft["status"] = "done" if result.get("ok") else "failed"
    draft["result"] = result
    audit.record("sent" if result.get("ok") else "failed", draft, log,
                 detail=result.get("detail", ""), path=result.get("path"))
    return result
