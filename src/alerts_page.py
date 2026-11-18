"""Alerts page (``view == "alerts"``, Admin page permission).

Create, change, test and delete alerts (``src/alerts.py``), see when each
runs next and what happened (log).
"""

import datetime as dt
import html

import streamlit as st

from src import alerts
from src import settings as app_settings
from src.action_card import self_address_for
from src.actions import render

STATUS = {"sent": ("Sent", ":material/mark_email_read:"),
          "no_match": ("Nothing matched, no email", ":material/check_circle:"),
          "not_sent": ("Not sent", ":material/block:"),
          "failed": ("Sending failed", ":material/error:"),
          "error": ("Check failed", ":material/error:"),
          "running": ("Running", ":material/hourglass_top:")}


def _recipient_options(settings, self_address):
    options = {}
    if self_address:
        options["me"] = f"Me <{self_address}>"
    for entry in settings["actions"]["address_book"]:
        options[entry["email"]] = f"{entry['name']} <{entry['email']}>"
    return options


def _form(key, alert, settings, self_address):
    """The alert's fields. Returns the values when submitted, else ``None``."""

    options = _recipient_options(settings, self_address)
    for r in alert.get("recipients", []):
        options.setdefault(r, r)
    with st.form(key, border=False):
        a, b = st.columns([1, 2])
        name = a.text_input("Name", value=alert.get("name", ""), key=f"{key}_name",
                            placeholder="Stores below target")
        question = b.text_input("Data question", value=alert.get("question", ""),
                                key=f"{key}_question",
                                placeholder="Target achievement % by store this month",
                                help="Asked like a chat question, each time the alert runs.")
        condition = st.text_input(
            "Send only when", value=alert.get("condition", ""), key=f"{key}_condition",
            placeholder="below 80%",
            help="'below 80%', 'above 100000', 'at least 95%', 'at most 5000'. Applied "
                 "to the answer's percentage column (with %) or its last number column. "
                 "Leave empty to send the answer every time (a scheduled report).")
        c, d = st.columns([1, 2])
        hour, minute = map(int, (alert.get("time") or "08:00").split(":"))
        when = c.time_input("Time", value=dt.time(hour, minute), key=f"{key}_time",
                            step=dt.timedelta(minutes=15))
        days = d.multiselect("Days", list(range(7)), default=alert.get("days", [0, 1, 2, 3, 6]),
                             format_func=lambda i: alerts.DAYS[i], key=f"{key}_days")
        recipients = st.multiselect("Send to", list(options),
                                    default=[r for r in alert.get("recipients", ["me"])
                                             if r in options],
                                    format_func=options.get, key=f"{key}_recipients",
                                    help="People in the address book (Admin → Actions).")
        e, f = st.columns(2)
        attach = e.checkbox("Attach the rows as Excel", value=alert.get("attach", True),
                            key=f"{key}_attach")
        enabled = f.checkbox("On", value=alert.get("enabled", True), key=f"{key}_enabled")
        submitted = st.form_submit_button("Save alert", type="primary", icon=":material/save:")
    if not submitted:
        return None
    return {**alert, "name": name, "question": question, "condition": condition,
            "time": when.strftime("%H:%M"), "days": days, "recipients": recipients,
            "attach": attach, "enabled": enabled}


def _save(values, settings, self_address, me, notice):
    actions = settings["actions"]
    try:
        clean = alerts.clean(values, actions["address_book"], actions["allowed_domains"],
                             self_address)
    except alerts.AlertError as exc:
        st.error(str(exc), icon=":material/error:")
        return False
    stored = alerts.load()
    if clean.get("id"):
        stored = [clean if a["id"] == clean["id"] else a for a in stored]
    else:
        if len(stored) >= alerts.MAX_ALERTS:
            st.error(f"At most {alerts.MAX_ALERTS} alerts.", icon=":material/error:")
            return False
        clean = {**clean, "id": alerts.new_id(), "created_by": me,
                 "created": dt.datetime.now().isoformat(timespec="seconds")}
        stored.append(clean)
    alerts.save(stored)
    st.session_state.alerts_notice = notice
    return True


def _show_result(result, alert):
    if result["context"] is None or result["problem"]:
        st.error(result["problem"], icon=":material/error:")
        return
    rows = result["rows"]
    if alert["condition"] and not rows:
        st.success(f"No row where {result['described']}: no email would be sent.",
                   icon=":material/check_circle:")
        return
    st.info((f"{len(rows)} row(s) where {result['described']}" if alert["condition"]
             else f"{len(rows)} row(s)") + ": this is what the email would contain.",
            icon=":material/mail:")
    context = dict(result["context"], rows=rows)
    st.dataframe([{n: render.cell(context, r, c) for n, c in
                   zip(render.labels(context), context["columns"])} for r in rows[:50]],
                 hide_index=True, use_container_width=True)


def _alert_card(alert, settings, secrets, self_address, me, now):
    status, icon = STATUS.get(alert.get("last_status"), ("Not run yet", ":material/schedule:"))
    upcoming = alerts.next_run(alert, now)
    days = ", ".join(alerts.DAYS[d] for d in alert["days"])
    with st.container(border=True, key=f"alert_card_{alert['id']}"):
        top, switch = st.columns([5, 1], vertical_alignment="center")
        top.html(f'<div class="ip-card-title">{html.escape(alert["name"])}'
                 f'{"" if alert["enabled"] else " · off"}</div>'
                 f'<div class="ip-card-sub">{html.escape(alert["question"])}'
                 + (f' · when {html.escape(alert["condition"])}' if alert["condition"]
                    else " · every time") + f' · {alert["time"]} on {days}</div>')
        if switch.toggle("On", value=alert["enabled"], key=f"alert_on_{alert['id']}") \
                != alert["enabled"]:
            stored = alerts.load()
            for item in stored:
                if item["id"] == alert["id"]:
                    item["enabled"] = not alert["enabled"]
            alerts.save(stored)
            st.rerun()
        st.caption(f"Last: {status}"
                   + (f" ({alert['last_run']})" if alert.get("last_run") else "")
                   + (f" · {alert['last_detail'][:160]}" if alert.get("last_detail") else "")
                   + (f" · next: {upcoming:%a %d %b %H:%M}" if upcoming else ""))

        test, send, edit, delete = st.columns(4)
        if test.button("Test (no email)", key=f"alert_test_{alert['id']}",
                       icon=":material/science:", use_container_width=True):
            with st.spinner("Asking the question..."):
                try:
                    result = alerts.evaluate(alert, settings)
                except Exception as exc:
                    result = {"context": None, "rows": [], "described": "",
                              "problem": f"The check failed: {exc}"}
            _show_result(result, alert)
        if send.button("Run now", key=f"alert_run_{alert['id']}", icon=":material/send:",
                       use_container_width=True,
                       help="Checks now and emails if the condition is met."):
            with st.spinner("Checking and sending..."):
                entry = alerts.run(alert, settings, secrets, self_address, by=me)
            label, entry_icon = STATUS.get(entry["status"], (entry["status"], None))
            (st.success if entry["status"] in ("sent", "no_match") else st.error)(
                f"{label}. {entry['detail']}", icon=entry_icon)
        with edit.popover("Edit", icon=":material/edit:", use_container_width=True):
            values = _form(f"alert_edit_{alert['id']}", alert, settings, self_address)
            if values is not None and _save(values, settings, self_address, me,
                                            f"Alert **{alert['name']}** saved."):
                st.rerun()
        with delete.popover("Delete", icon=":material/delete:", use_container_width=True):
            st.write(f"Delete the alert **{alert['name']}**?")
            if st.button("Delete for good", key=f"alert_delete_{alert['id']}", type="primary"):
                alerts.save([a for a in alerts.load() if a["id"] != alert["id"]])
                st.session_state.alerts_notice = f"Alert **{alert['name']}** deleted."
                st.rerun()


def render(me, now=None):
    now = now or dt.datetime.now()
    settings = app_settings.load()
    secrets = app_settings.load_secrets()
    self_address = self_address_for(settings)

    title, back = st.columns([5, 1.3], vertical_alignment="bottom")
    with title:
        st.html('<div class="ip-card-title" style="font-size:1.25rem">Alerts</div>'
                '<div class="ip-card-sub">A data question checked every day at a set '
                'time; an email goes out when the answer meets the condition.</div>')
    with back:
        if st.button("Back", key="alerts_back", icon=":material/arrow_back:",
                     use_container_width=True):
            st.session_state.view = "chat"
            st.query_params.clear()
            st.rerun()

    notice = st.session_state.pop("alerts_notice", None)
    if notice:
        st.success(notice, icon=":material/check_circle:")
    actions = settings["actions"]
    if not (actions["enabled"] and actions["kinds"].get("email")):
        st.warning("Email actions are switched off in Admin → Actions, so alerts cannot "
                   "send anything (Test still works).", icon=":material/mail_lock:")
    worker = alerts.worker_status(now)
    message = ("Alerts are checked every minute while InsightPilot is running"
               + (f" (last check {worker['last_tick']:%H:%M})" if worker["last_tick"] else "")
               + ". If the computer is off at an alert's time, it runs when the app is "
               "next running that day.")
    if worker["running"]:
        st.caption(message + (f" Last error: {worker['last_error']}"
                              if worker["last_error"] else ""))
    else:
        st.warning("The alerts checker is not running, so alerts will not be sent. Use "
                   "the Restart InsightPilot shortcut (it starts the checker with the "
                   "app)." + (f" Last check: {worker['last_tick']:%Y-%m-%d %H:%M}."
                              if worker["last_tick"] else ""),
                   icon=":material/notifications_off:")

    with st.expander("New alert", icon=":material/add_alert:", expanded=not alerts.load()):
        values = _form("alert_new", {"time": "08:00", "days": [0, 1, 2, 3, 6],
                                     "recipients": ["me"] if self_address else []},
                       settings, self_address)
        if values is not None and _save(values, settings, self_address, me,
                                        f"Alert **{values['name'].strip()}** created."):
            st.rerun()

    for alert in alerts.load():
        _alert_card(alert, settings, secrets, self_address, me, now)

    history = list(reversed(alerts.entries(limit=200)))
    if history:
        with st.expander("History", icon=":material/history:"):
            st.dataframe([{"Time": h["time"].replace("T", " "), "Alert": h.get("name"),
                           "Result": STATUS.get(h["status"], (h["status"],))[0],
                           "Rows": h.get("rows", 0), "By": h.get("by", ""),
                           "Detail": h.get("detail", "")} for h in history],
                         hide_index=True, use_container_width=True, height=320)
