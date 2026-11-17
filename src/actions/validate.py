"""Checks a draft must pass before it can be approved (and again on Approve,
after the user's edits). Each problem is a sentence the user can act on."""

from src.actions import address_book, audit, render
from src.actions.executors import webhook_url_problem

MAX_BODY_BYTES = 200_000
MAX_ATTACHMENT_BYTES = 3_000_000      # Graph sendMail limit is ~4 MB per request
MAX_TASKS = 20
MAX_SUBJECT = 200
MAX_NOTE = 2000
ORIGIN = "user_message"


def problems(draft, settings, secrets=None, self_address="", log=None, today=None):
    actions = settings["actions"]
    kind = draft.get("kind")
    found = []
    if not actions["enabled"]:
        found.append("Actions are switched off (Admin -> Actions).")
    if kind not in actions["kinds"] or not actions["kinds"].get(kind):
        found.append(f"This kind of action ({kind}) is switched off in Admin.")
    if draft.get("origin") != ORIGIN:
        found.append("Actions can only come from your own chat message.")
    context = draft.get("context") or {}
    if "rows" not in context:
        found.append("There is no answer to act on: ask a data question first.")
        return found
    if len(draft.get("note", "")) > MAX_NOTE:
        found.append(f"The note is longer than {MAX_NOTE} characters.")
    if kind in ("email", "teams_post") and not draft.get("subject", "").strip():
        found.append("The subject is empty.")
    if len(draft.get("subject", "")) > MAX_SUBJECT:
        found.append(f"The subject is longer than {MAX_SUBJECT} characters.")

    if kind == "email":
        book = actions["address_book"]
        domains = actions["allowed_domains"]
        recipients = draft.get("recipients", [])
        if not recipients:
            found.append("Choose at least one recipient.")
        if len(recipients) > actions["max_recipients"]:
            found.append(f"At most {actions['max_recipients']} recipients per email.")
        for r in recipients:
            if not address_book.allowed(r.get("email"), book, domains, self_address):
                found.append(f"{r.get('email')} is not in the address book or an "
                             "allowed domain.")
        for item in draft.get("unresolved", []):
            found.append(f"'{item['phrase']}': {item['reason']}.")
        if len(render.email_html(context, draft.get("note", "")).encode()) > MAX_BODY_BYTES:
            found.append("The message is too large; send the table as an export instead.")
        if draft.get("attach") and context["rows"]:
            size = len(render.xlsx_bytes(context) if draft.get("format") != "csv"
                       else render.csv_bytes(context))
            if size > MAX_ATTACHMENT_BYTES:
                found.append("The attachment is larger than 3 MB; untick it.")
    elif kind == "teams_post":
        problem = webhook_url_problem((secrets or {}).get("teams_webhook_url", ""))
        if problem:
            found.append(problem)
    elif kind == "task":
        tasks = draft.get("tasks", [])
        if not tasks:
            found.append("No tasks to create" + (
                f" ({draft['filter_problem']})" if draft.get("filter_problem") else
                " (no row of the answer matches)") + ".")
        if len(tasks) > MAX_TASKS:
            found.append(f"{len(tasks)} tasks: at most {MAX_TASKS} per request. Narrow "
                         "the question or the condition.")
        if any(not t.get("title", "").strip() for t in tasks):
            found.append("Every task needs a title.")
    elif kind == "export":
        if not context["rows"]:
            found.append("The answer has no rows to export.")

    cap = actions["daily_caps"].get(kind)
    if cap is not None:
        used = audit.used_today(kind, log, today)
        if used + audit.units(draft) > cap:
            found.append(f"Daily limit reached for {kind} ({used} of {cap} used "
                         "today; change it in Admin -> Actions).")
    return found
