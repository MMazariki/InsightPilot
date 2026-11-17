"""Append-only log of every action draft, approval, edit, cancel and result.

One JSON object per line in ``logs/actions.jsonl`` (gitignored). Message
bodies and table values are not logged: only who, what kind, the subject or
task titles, and the outcome.
"""

import datetime as dt
import json
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "logs" / "actions.jsonl"
EVENTS = ("drafted", "approved", "sent", "failed", "refused", "cancelled",
          "expired", "superseded")
_LOCK = threading.Lock()


def record(event, draft, log=None, **detail):
    if event not in EVENTS:
        raise ValueError(f"Unknown audit event {event!r}")
    entry = {
        "time": dt.datetime.now().isoformat(timespec="seconds"),
        "event": event,
        "draft_id": draft.get("id"),
        "kind": draft.get("kind"),
        "request": draft.get("request", "")[:300],
        "recipients": [r["email"] for r in draft.get("recipients", [])],
        "subject": draft.get("subject", ""),
        "count": units(draft),
        **detail,
    }
    path = Path(log) if log else LOG
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")
    return entry


def units(draft):
    """How much of the daily limit one execution uses."""

    return len(draft.get("tasks", [])) if draft.get("kind") == "task" else 1


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


def used_today(kind, log=None, today=None):
    today = (today or dt.date.today()).isoformat()
    return sum(e.get("count", 1) for e in entries(log)
               if e.get("event") == "sent" and e.get("kind") == kind
               and str(e.get("time", "")).startswith(today))
