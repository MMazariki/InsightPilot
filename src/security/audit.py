"""Security log: sign-ins, failures, lockouts and changes to users.

One JSON object per line in ``logs/security.jsonl``. Passwords and hashes
are never written.
"""

import datetime as dt
import json
import threading
from pathlib import Path

from src import logfiles

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "logs" / "security.jsonl"
EVENTS = ("setup", "signed_in", "resumed", "sign_in_failed", "locked", "signed_out",
          "session_expired", "user_created", "user_updated", "user_deleted",
          "password_changed", "password_reset", "folders_changed", "denied")
_LOCK = threading.Lock()


def record(event, user="", by="", log=None, **detail):
    if event not in EVENTS:
        raise ValueError(f"Unknown security event {event!r}")
    entry = {"time": dt.datetime.now().isoformat(timespec="seconds"),
             "event": event, "user": user, "by": by, **detail}
    path = Path(log) if log else LOG
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        logfiles.rotate(path)
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
