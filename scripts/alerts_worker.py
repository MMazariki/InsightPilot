"""InsightPilot alerts worker: checks the alerts every minute.

Started hidden by ``InsightPilot-Server.ps1`` next to the app, stopped by
``Stop-InsightPilot.ps1`` (and when ``logs\\stop.flag`` appears). Only one
worker runs at a time (a lock on ``logs\\alerts_worker.lock``); a second
one exits at once. Output goes to ``logs\\alerts_worker.log``.

    .venv\\Scripts\\python.exe scripts\\alerts_worker.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _single_instance(path):
    """Hold an exclusive lock on ``path`` for the life of the process."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def main():
    lock = _single_instance(ROOT / "logs" / "alerts_worker.lock")
    if lock is None:
        print("Another alerts worker is running.")
        return 0
    from src import alerts

    print("Alerts worker started.", flush=True)
    alerts.work(stop_flag=ROOT / "logs" / "stop.flag")
    print("Stop flag found; alerts worker stopped.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
