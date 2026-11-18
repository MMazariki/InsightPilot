"""Alerts: saved data questions checked on a schedule, emailed when a
condition is met.

An alert is::

    {"id": "a1b2c3", "name": "Stores below target",
     "question": "Target achievement % by store this month",
     "condition": "below 80%",          # "" = always send (a daily report)
     "recipients": ["me", "sara@company.com"],
     "time": "08:00", "days": [0, 1, 2, 3, 6],   # Monday = 0
     "attach": true, "enabled": true, "created_by": "maz",
     "last_run": "2026-11-19", "last_status": "sent", "last_detail": "..."}

Stored in ``config/alerts.json``; every check is logged in
``logs/alerts.jsonl``. A worker process (``scripts/alerts_worker.py``,
started and stopped with the app server) checks every minute and runs each
enabled alert once a day, at or after its time, on its days. If the
computer or the app was off at that time, the alert runs when the app is
next running that day.

How a check works:

1. The question is answered by the same local pipeline as the chat
   (read-only Power BI query).
2. The condition ("below 80%", "above 100000", "at least 95%") is applied
   with the same rule as "a task for every store below 85%"
   (``render.filter_rows``).
3. If rows match (or there is no condition), one email goes to the
   alert's recipients with only the matching rows, the chart and
   optionally the table as Excel.

Creating the alert is the approval: whoever has the Alerts permission
chose the question, the condition and the recipients. Every email is still
checked like a chat action (address book or allowed domains only, at most
10 recipients, the sending account set up, the daily email limit) and is
counted in the action log, so alerts and chat emails share the limit.
"""

import datetime as dt
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALERTS_FILE = ROOT / "config" / "alerts.json"
LOG = ROOT / "logs" / "alerts.jsonl"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MAX_ALERTS = 50
_TIME = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")
_LOCK = threading.Lock()      # one check at a time (scheduler or Run now)


class AlertError(ValueError):
    """An alert that cannot be saved as it is (shown on the page)."""


# ------------------------------------------------------------------ store

def load(path=None):
    path = Path(path) if path else ALERTS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        return []
    return [a for a in data.get("alerts", []) if isinstance(a, dict) and a.get("id")]


def save(alerts, path=None):
    path = Path(path) if path else ALERTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"version": 1, "alerts": alerts}, indent=2,
                               ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def parse_condition(text):
    """``"below 80%"`` -> row filter dict; ``""`` -> ``None``; else AlertError."""

    from src.actions.detect import _row_filter

    text = (text or "").strip()
    if not text:
        return None
    found = _row_filter(text)
    if found is None:
        raise AlertError("Write the condition like 'below 80%', 'above 100000', "
                         "'at least 95%' or 'at most 5000' (or leave it empty to "
                         "send every time).")
    return found


def split_condition(question, condition):
    """A condition written into the question ("Target achievement % by store
    below 80%") is moved to the condition when that is empty: the question
    is asked without it and the rows are filtered with it. Returns
    ``(question, condition)``."""

    from src.actions.detect import _FILTER

    question = re.sub(r"\s+", " ", str(question or "")).strip()
    condition = str(condition or "").strip()
    if condition:
        return question, condition
    match = _FILTER.search(question)
    if not match:
        return question, ""
    rest = (question[:match.start()] + " " + question[match.end():])
    rest = re.sub(r"\b(?:is|are|was|were|that|which|where|when|with)\s*$", "",
                  rest.strip(" ,.?"), flags=re.I)
    rest = re.sub(r"\s+", " ", rest).strip(" ,.?")
    return (rest or question), match.group(0).strip()


def clean(alert, book=(), domains=(), self_address=""):
    """A checked copy of ``alert`` from the page, or AlertError."""

    from src.actions import address_book

    name = str(alert.get("name") or "").strip()
    question, condition = split_condition(alert.get("question"), alert.get("condition"))
    alert = {**alert, "condition": condition}
    if not name or len(name) > 80:
        raise AlertError("Give the alert a name (80 characters at most).")
    if not question or len(question) > 300:
        raise AlertError("Write the data question to check (300 characters at most).")
    parse_condition(alert.get("condition"))
    when = str(alert.get("time") or "").strip()
    if not _TIME.fullmatch(when):
        raise AlertError("The time must be HH:MM, for example 08:00.")
    days = sorted({int(d) for d in alert.get("days") or [] if 0 <= int(d) <= 6})
    if not days:
        raise AlertError("Choose at least one day.")
    recipients = [str(r).strip().lower() for r in alert.get("recipients") or [] if str(r).strip()]
    if not recipients:
        raise AlertError("Choose at least one recipient.")
    for r in recipients:
        address = self_address if r == "me" else r
        if not address or not address_book.allowed(address, list(book), list(domains),
                                                    self_address):
            raise AlertError(f"{r} is not in the address book or an allowed domain.")
    return {**alert, "name": name, "question": question,
            "condition": str(alert.get("condition") or "").strip(), "time": when,
            "days": days, "recipients": recipients, "attach": bool(alert.get("attach", True)),
            "enabled": bool(alert.get("enabled", True))}


def new_id():
    return secrets.token_hex(4)


# --------------------------------------------------------------- schedule

def due(alert, now):
    """True when ``alert`` should run at ``now`` (local time)."""

    if not alert.get("enabled") or now.weekday() not in alert.get("days", []):
        return False
    if alert.get("last_run") == now.date().isoformat():
        return False
    match = _TIME.fullmatch(alert.get("time", ""))
    if not match:
        return False
    return (now.hour, now.minute) >= (int(match.group(1)), int(match.group(2)))


def next_run(alert, now):
    """The next time ``alert`` will run (for the page), or ``None``."""

    if not alert.get("enabled") or not alert.get("days"):
        return None
    hour, minute = map(int, alert["time"].split(":"))
    for ahead in range(8):
        day = now.date() + dt.timedelta(days=ahead)
        if day.weekday() not in alert["days"]:
            continue
        moment = dt.datetime.combine(day, dt.time(hour, minute))
        if ahead == 0 and alert.get("last_run") == day.isoformat():
            continue
        return max(moment, now) if ahead == 0 else moment
    return None


# ------------------------------------------------------------------ check

def record(alert, status, detail="", rows=0, by="", log=None):
    entry = {"time": dt.datetime.now().isoformat(timespec="seconds"),
             "alert": alert.get("id"), "name": alert.get("name"), "status": status,
             "rows": rows, "detail": str(detail)[:400], "by": by}
    path = Path(log) if log else LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def entries(log=None, limit=None):
    path = Path(log) if log else LOG
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out[-limit:] if limit else out


def _default_agent(settings):
    from src import agent_factory
    from src.structured_agent import StructuredPlanningAgent

    return StructuredPlanningAgent(**agent_factory.structured_agent_kwargs(settings))


def evaluate(alert, settings, agent_factory=None, now=None):
    """Answer the question and apply the condition. Returns
    ``{"context": ..., "rows": [...matching], "described": str, "problem": str}``
    (``context`` is ``None`` when the question got no data answer)."""

    from src.actions import render, service

    now = now or dt.datetime.now()
    question, condition = split_condition(alert["question"], alert.get("condition"))
    alert = {**alert, "question": question, "condition": condition}
    agent = (agent_factory or _default_agent)(settings)
    try:
        answer = agent.ask(alert["question"])
        trace = getattr(agent, "last_trace", None) or {}
        context = service.context_from(alert["question"], getattr(agent, "last_result", None),
                                       trace.get("interpreted", []),
                                       now.strftime("%Y-%m-%d %H:%M"))
    finally:
        try:
            agent.close()
        except Exception:
            pass
    if context is None:
        text = re.sub(r"\s+", " ", str(answer or "")).strip()
        return {"context": None, "rows": [], "described": "",
                "problem": "The question got no data answer: " + text[:200]}
    rows, described, problem = render.filter_rows(context, parse_condition(alert["condition"]))
    return {"context": context, "rows": rows, "described": described, "problem": problem}


def _email_draft(alert, result, settings, self_address):
    from src.actions import service

    context = dict(result["context"], rows=result["rows"])
    if alert["condition"]:
        note = (f"Alert '{alert['name']}': {len(result['rows'])} row"
                f"{'s' if len(result['rows']) != 1 else ''} where {result['described']}.")
    else:
        note = f"Scheduled report '{alert['name']}'."
    parsed = {"kind": "email", "question": None, "recipients": list(alert["recipients"]),
              "note": note, "format": "xlsx", "per_row": False, "row_filter": None,
              "task_text": "", "due": None}
    draft = service.create_draft(parsed, context, f"[Alert: {alert['name']}]", settings,
                                 self_address)
    draft["subject"] = (f"InsightPilot alert: {alert['name']}" if alert["condition"]
                        else f"InsightPilot report: {alert['name']}")[:200]
    draft["attach"] = bool(alert.get("attach")) and bool(context["rows"])
    return draft


def run(alert, settings, secrets_=None, self_address="", by="schedule",
        agent_factory=None, send=None, now=None, log=None, action_log=None):
    """Check one alert and email if it matches. Returns the log entry.
    ``send``: the email executor (tests pass a fake)."""

    from src.actions import audit, executors
    from src.actions.validate import problems

    now = now or dt.datetime.now()
    # Alerts saved before conditions were split out of the question.
    question, condition = split_condition(alert["question"], alert.get("condition"))
    alert = {**alert, "question": question, "condition": condition}
    with _LOCK:
        try:
            result = evaluate(alert, settings, agent_factory, now)
        except Exception as exc:
            return record(alert, "error", f"The check failed: {exc}", by=by, log=log)
        if result["context"] is None or result["problem"]:
            return record(alert, "error", result["problem"], by=by, log=log)
        if alert["condition"] and not result["rows"]:
            return record(alert, "no_match", f"No row where {result['described']}.",
                          by=by, log=log)
        if not result["rows"]:
            return record(alert, "no_match", "The answer has no rows: nothing to send.",
                          by=by, log=log)

        draft = _email_draft(alert, result, settings, self_address)
        found = problems(draft, settings, secrets_, self_address, log=action_log)
        if found:
            audit.record("refused", draft, action_log, problems=found)
            return record(alert, "not_sent", " ".join(found), len(result["rows"]),
                          by=by, log=log)
        outcome = (send or executors.run_email)(draft, settings, secrets=secrets_)
        audit.record("sent" if outcome.get("ok") else "failed", draft, action_log,
                     detail=outcome.get("detail", ""), alert=alert["id"])
        return record(alert, "sent" if outcome.get("ok") else "failed",
                      outcome.get("detail", ""), len(result["rows"]), by=by, log=log)


def mark_run(alert_id, entry, day, path=None):
    """Remember the run on the stored alert. ``day`` is the date the check
    started (a check that ends after midnight still counts for that day)."""

    alerts = load(path)
    for alert in alerts:
        if alert["id"] == alert_id:
            alert["last_run"] = day.isoformat()
            alert["last_status"] = entry["status"]
            alert["last_detail"] = entry["detail"]
    save(alerts, path)


# -------------------------------------------------------------- scheduler

def run_due(now=None, **kwargs):
    """Run every alert that is due. Used by the scheduler each minute."""

    from src import settings as app_settings
    from src.action_card import self_address_for

    now = now or dt.datetime.now()
    alerts = [a for a in load() if due(a, now)]
    if not alerts:
        return []
    settings = app_settings.load()
    secrets_ = app_settings.load_secrets()
    self_address = self_address_for(settings)
    done = []
    for alert in alerts:
        # Marked first: a failing check is not retried every minute.
        mark_run(alert["id"], {"status": "running", "detail": ""}, now.date())
        entry = run(alert, settings, secrets_, self_address, now=now, **kwargs)
        mark_run(alert["id"], entry, now.date())
        done.append(entry)
    return done


HEARTBEAT = ROOT / "logs" / "alerts_worker.json"


def work(interval=60, stop_flag=None, ticks=None):
    """The checker loop of ``scripts/alerts_worker.py`` (a process of its
    own, started with the app by ``InsightPilot-Server.ps1``, so alerts run
    even when nobody has the app open). Writes a heartbeat each minute."""

    started = dt.datetime.now().isoformat(timespec="seconds")
    count = 0
    while not (stop_flag and Path(stop_flag).exists()):
        error = ""
        try:
            run_due()
        except Exception as exc:              # never let the loop die
            error = str(exc)[:300]
        beat = {"pid": os.getpid(), "started": started,
                "last_tick": dt.datetime.now().isoformat(timespec="seconds"),
                "last_error": error}
        try:
            HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
            HEARTBEAT.write_text(json.dumps(beat), encoding="utf-8")
        except OSError:
            pass
        count += 1
        if ticks is not None and count >= ticks:
            return
        time.sleep(interval)


def worker_status(now=None):
    """``{"running": bool, "last_tick": datetime|None, "last_error": str}``:
    running means a heartbeat in the last three minutes."""

    now = now or dt.datetime.now()
    try:
        beat = json.loads(HEARTBEAT.read_text(encoding="utf-8"))
        tick = dt.datetime.fromisoformat(beat["last_tick"])
    except (OSError, ValueError, KeyError, TypeError):
        return {"running": False, "last_tick": None, "last_error": ""}
    return {"running": (now - tick).total_seconds() < 180, "last_tick": tick,
            "last_error": beat.get("last_error", "")}
