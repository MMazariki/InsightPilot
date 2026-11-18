"""Chat side of actions: turn a request into a draft, show the approval card,
run the approved action (docs/AGENT_ACTIONS_PLAN.md).

Session state used:

* ``action_context``  the latest data answer (question, columns, rows)
* ``action_drafts``   ``{draft_id: draft}`` for this chat
* ``action_go``       the draft id whose Approve button was clicked

A draft is executed only in a run that follows a click on its Approve button,
and only once (``service.approve`` enforces it).
"""

import datetime as dt
import html
from pathlib import Path

import streamlit as st

from src import settings as app_settings
from src.actions import address_book, service
from src.actions import render as content
from src.actions.validate import problems

DRAFT_MESSAGE = {
    "email": "Here is the email. **Nothing is sent until you click Approve and send.**",
    "teams_post": "Here is the Teams post. **Nothing is posted until you click "
                  "Approve and post.**",
    "task": "Here are the To Do tasks. **Nothing is created until you click Approve.**",
    "export": "Here is the export. **The file is saved when you click Approve.**",
}
APPROVE_LABEL = {"email": "Approve and send", "teams_post": "Approve and post",
                 "task": "Approve and create", "export": "Approve and save"}


def init_state():
    st.session_state.setdefault("action_context", None)
    st.session_state.setdefault("action_drafts", {})
    st.session_state.setdefault("action_go", None)


def remember_answer(question, agent):
    """Keep the latest data answer as the subject of the next action."""

    trace = getattr(agent, "last_trace", None) or {}
    context = service.context_from(
        question, getattr(agent, "last_result", None), trace.get("interpreted", []),
        dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
    # A declined or failed question leaves nothing to act on: "email this"
    # must never pick up an older answer.
    st.session_state.action_context = context
    supersede()


def supersede():
    """A new question ends any draft still waiting (plan §4.2)."""

    for draft in st.session_state.action_drafts.values():
        service.close(draft, "superseded")


def start(parsed, prompt, settings, self_address):
    """Create a draft for ``parsed`` on the current answer. Returns the chat
    message for it (a dict for ``st.session_state.messages``)."""

    supersede()
    if not settings["actions"]["enabled"]:
        return {"role": "assistant", "content":
                "That looks like a request to "
                + service.KIND_LABELS[parsed["kind"]].lower()
                + ". Actions are switched off: an administrator can turn them on in "
                "**Admin → Actions** (with an address book and limits). I can only "
                "answer questions until then."}
    if not settings["actions"]["kinds"].get(parsed["kind"]):
        return {"role": "assistant", "content":
                f"{service.KIND_LABELS[parsed['kind']]} actions are switched off in "
                "Admin → Actions."}
    context = st.session_state.action_context
    if context is None:
        return {"role": "assistant", "content":
                "There is no answer to act on yet. Ask a data question first (for "
                "example *Net sales by region in 2025*), then ask me to email, post, "
                "export it or create tasks from it."}
    draft = service.create_draft(parsed, context, prompt, settings, self_address)
    st.session_state.action_drafts[draft["id"]] = draft
    return {"role": "assistant", "content": DRAFT_MESSAGE[draft["kind"]],
            "action_id": draft["id"]}


# ------------------------------------------------------- quick buttons

def quick_kinds(settings):
    """The action buttons offered under an answer: ``[(kind, label, icon)]``,
    only for the kinds switched on in Admin → Actions."""

    actions = settings["actions"]
    if not actions["enabled"]:
        return []
    task_label = "Reminder" if actions["task_via"] == "ics" else "To Do task"
    offered = [("email", "Email", ":material/mail:"),
               ("export", "Excel", ":material/table_view:"),
               ("task", task_label, ":material/event:"),
               ("teams_post", "Teams", ":material/forum:")]
    return [o for o in offered if actions["kinds"].get(o[0])]


def button_request(kind, self_address=""):
    """The same draft request a typed message would give, for a button:
    email to yourself ("me", changed on the card), Excel, no due date."""

    recipients = ["me"] if kind == "email" and self_address else []
    return {"kind": kind, "question": None, "recipients": recipients, "note": "",
            "format": "xlsx", "per_row": False, "row_filter": None,
            "task_text": "", "due": None}


def _click_quick(kind, label, settings, self_address):
    st.session_state.messages.append(start(
        button_request(kind, self_address), f"[{label} button]", settings, self_address))


def quick_buttons(settings, self_address, key):
    """A row of buttons (Email, Excel, Reminder, ...) under the latest data
    answer. Each one opens the same approval card as typing the request."""

    kinds = quick_kinds(settings)
    if not kinds or st.session_state.action_context is None:
        return
    columns = st.columns(len(kinds) + 1)
    for column, (kind, label, icon) in zip(columns, kinds):
        column.button(label, key=f"{key}_{kind}", icon=icon,
                      help=f"{service.KIND_LABELS[kind]}: opens a draft to check; "
                           "nothing happens before you approve it.",
                      on_click=_click_quick, args=(kind, label, settings, self_address),
                      use_container_width=True)


# ------------------------------------------------------------- the card

def _k(draft, name):
    return f"act_{draft['id']}_{name}"


def _edits(draft):
    """The card's current field values (from widget state)."""

    state = st.session_state
    edits = {}
    for name in ("subject", "note", "attach", "format", "extra", "recipients"):
        if _k(draft, name) in state:
            edits[name] = state[_k(draft, name)]
    if _k(draft, "tasks") in state:
        edits["tasks"] = state[_k(draft, "tasks")]
        due = state.get(_k(draft, "due"))
        edits["due"] = due.isoformat() if (due and state.get(_k(draft, "has_due"))) else ""
    # Recipients count as edited only once the user changes them, so phrases
    # that matched nobody stay listed as problems until then.
    unchanged = (edits.get("recipients") == [r["email"] for r in draft["recipients"]]
                 and not (edits.get("extra") or "").strip())
    if unchanged:
        edits.pop("recipients", None)
        edits.pop("extra", None)
    elif "recipients" in edits:
        edits.setdefault("extra", "")
    return edits


def _click_approve(draft_id):
    st.session_state.action_go = draft_id


def _click_cancel(draft_id):
    draft = st.session_state.action_drafts.get(draft_id)
    service.close(draft, "cancelled")


def _recipient_options(draft, settings, self_address):
    book = settings["actions"]["address_book"]
    options = {e["email"]: f"{e['name']} <{e['email']}>"
               + (f" · {e['role']}" if e.get("role") else "") for e in book}
    if self_address:
        options.setdefault(self_address.lower(), f"Me <{self_address.lower()}>")
    for r in draft["recipients"]:
        options.setdefault(r["email"], f"{r['name']} <{r['email']}>")
    return options


def render(draft, settings, self_address):
    """Draw one draft: editable card while pending, outcome afterwards."""

    if draft["status"] == "pending" and service.expired(draft):
        service.close(draft, "expired")
    if draft["status"] != "pending":
        _outcome(draft)
        return

    if st.session_state.action_go == draft["id"]:
        st.session_state.action_go = None
        service.apply_edits(draft, _edits(draft), settings, self_address)
        with st.spinner("Working on it..."):
            result = service.approve(draft, settings, app_settings.load_secrets(),
                                     self_address)
        if draft["status"] != "pending":
            _outcome(draft)
            return
        if not result.get("ignored"):
            st.error(result.get("detail", "Not approved."), icon=":material/block:")

    context = draft["context"]
    kind = draft["kind"]
    expires = dt.datetime.fromtimestamp(draft["expires_ts"]).strftime("%H:%M")
    with st.container(border=True, key=f"act_card_{draft['id']}"):
        st.html(f'<div class="ip-card-title">{service.KIND_LABELS[kind]} · draft</div>'
                f'<div class="ip-card-sub">About: {html.escape(context["question"] or "")}'
                f" · expires at {expires}</div>")
        if kind == "email":
            options = _recipient_options(draft, settings, self_address)
            st.multiselect("To", list(options), default=[r["email"] for r in
                                                         draft["recipients"]],
                           format_func=options.get, key=_k(draft, "recipients"),
                           help="Only people in the address book (Admin → Actions).")
            st.text_input("Other addresses", key=_k(draft, "extra"),
                          placeholder="name@" + (settings["actions"]["allowed_domains"][0]
                                                 if settings["actions"]["allowed_domains"]
                                                 else "company.com"),
                          help="Comma-separated; only allowed domains: "
                               + (", ".join(settings["actions"]["allowed_domains"]) or "none"))
            if draft["unresolved"]:
                st.button("Ignore the names that matched nobody",
                          key=_k(draft, "drop_unresolved"), icon=":material/person_off:",
                          on_click=lambda: draft.update(unresolved=[]))
            st.text_input("Subject", value=draft["subject"], key=_k(draft, "subject"))
            st.text_area("Your note (optional)", value=draft["note"], height=68,
                         key=_k(draft, "note"))
            if context["rows"]:
                st.checkbox(f"Attach the table as {draft['format'].upper()}",
                            value=draft.get("attach", False), key=_k(draft, "attach"))
        elif kind == "teams_post":
            channel = settings["actions"].get("teams_channel_label") or "the configured channel"
            st.caption(f"Posts to {channel}.")
            st.text_input("Title", value=draft["subject"], key=_k(draft, "subject"))
            st.text_area("Your note (optional)", value=draft["note"], height=68,
                         key=_k(draft, "note"))
        elif kind == "task":
            if draft.get("filter"):
                st.caption(f"Rows where {draft['filter']}.")
            target = ("Reminders for your calendar, saved as an .ics file"
                      if settings["actions"]["task_via"] == "ics" else
                      f"Tasks for To Do list ‘{settings['actions']['todo_list_name']}’")
            st.text_area(f"{target} (one per line)", value="\n".join(t["title"] for t in draft["tasks"]),
                         height=min(68 + 24 * len(draft["tasks"]), 300),
                         key=_k(draft, "tasks"))
            due = next((t["due"] for t in draft["tasks"] if t.get("due")), "")
            has_due = st.checkbox("Due date", value=bool(due), key=_k(draft, "has_due"))
            if has_due:
                st.date_input("Due", value=dt.date.fromisoformat(due) if due
                              else dt.date.today() + dt.timedelta(days=1),
                              key=_k(draft, "due"), label_visibility="collapsed")
        elif kind == "export":
            st.radio("Format", ["xlsx", "csv"], index=0 if draft["format"] == "xlsx" else 1,
                     horizontal=True, key=_k(draft, "format"),
                     format_func={"xlsx": "Excel (.xlsx)", "csv": "CSV"}.get)
            st.caption(f"Saved to `{settings['actions']['export_folder']}` in the project.")

        # The draft as it would be approved with the fields as they are now.
        preview = {**draft, "recipients": list(draft["recipients"]),
                   "unresolved": list(draft["unresolved"]), "tasks": list(draft["tasks"])}
        service.apply_edits(preview, _edits(draft), settings, self_address)
        with st.expander("Preview exactly what will be " + {
                "email": "sent", "teams_post": "posted", "task": "created",
                "export": "saved"}[kind]):
            if kind == "email":
                st.caption("To: " + (", ".join(r["email"] for r in preview["recipients"])
                                     or "nobody yet"))
                st.caption("Subject: " + preview["subject"])
                st.html(content.email_html(context, preview["note"]))
            elif kind == "teams_post":
                st.markdown(f"**{preview['subject']}**")
                if preview["note"]:
                    st.text(preview["note"])
                st.caption(content.summary(context))
                _table(context, content.MAX_CARD_ROWS)
            elif kind == "task":
                for task in preview["tasks"]:
                    st.markdown("- " + task["title"].replace("*", "\\*")
                                + (f" · due {task['due']}" if task.get("due") else ""))
            else:
                st.caption(content.summary(context))
                _table(context, 20)

        found = problems(preview, settings, app_settings.load_secrets(), self_address)
        for problem in found:
            st.warning(problem, icon=":material/error:")
        approve_col, cancel_col, _ = st.columns([1.4, 1, 1.6])
        approve_col.button(APPROVE_LABEL[kind], key=_k(draft, "approve"), type="primary",
                           icon=":material/check:", disabled=bool(found),
                           on_click=_click_approve, args=(draft["id"],),
                           use_container_width=True)
        cancel_col.button("Cancel", key=_k(draft, "cancel"), icon=":material/close:",
                          on_click=_click_cancel, args=(draft["id"],),
                          use_container_width=True)
    # The user has now seen this exact card; only now can it be approved.
    draft["shown"] = True


def _table(context, limit):
    st.dataframe(
        [{name: content.cell(context, row, column) for name, column in
          zip(content.labels(context), context["columns"])} for row in context["rows"][:limit]],
        hide_index=True, use_container_width=True)


def _outcome(draft):
    status = draft["status"]
    label = service.KIND_LABELS[draft["kind"]]
    result = draft.get("result") or {}
    if status == "done":
        st.success(f"{label}: {result.get('detail', 'done')}", icon=":material/task_alt:")
        path = result.get("path")
        if path and Path(path).exists():
            data = Path(path).read_bytes()
            st.download_button("Download " + Path(path).name, data, file_name=Path(path).name,
                               key=f"act_dl_{draft['id']}", icon=":material/download:")
    elif status == "failed":
        st.error(f"{label} failed: {result.get('detail', '')}", icon=":material/error:")
    else:
        words = {"cancelled": "cancelled", "expired": "expired (not sent)",
                 "superseded": "closed when a new question was asked (not sent)",
                 "running": "in progress"}
        st.caption(f"{label} draft {words.get(status, status)}.")


def self_address_for(settings, query_account=None):
    """Where "email this to me" goes: the sending account when email goes out
    through SMTP, else the signed-in Microsoft account."""

    actions = settings["actions"]
    if actions["email_via"] == "smtp" and actions["smtp_username"]:
        return actions["smtp_username"]
    address = (query_account or settings["powerbi"].get("username") or "").strip().lower()
    return address if address_book.is_email(address) else ""
