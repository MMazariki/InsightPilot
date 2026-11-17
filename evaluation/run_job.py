"""Run one evaluation as a background job for the dashboard.

    python -m evaluation.run_job app|docs

Writes evaluation/results/.evaluation_job.json while running (one job at a
time: both evaluations use the local models) and removes it when done,
whatever happens. Output goes to logs/evaluation_<kind>.log.
"""

import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.eval_results import KINDS, LOCK, running_job  # noqa: E402


def main(kind):
    if kind not in KINDS:
        raise SystemExit(f"Unknown evaluation: {kind}")
    if running_job():
        raise SystemExit("Another evaluation is already running.")
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(json.dumps({
        "kind": kind, "pid": os.getpid(), "started_ts": time.time(),
        "started": dt.datetime.now().isoformat(timespec="seconds"),
        "log": str(ROOT / "logs" / f"evaluation_{kind}.log"),
    }), encoding="utf-8")
    try:
        if kind == "app":
            from evaluation.run_app_evaluation import run
        else:
            from evaluation.run_doc_evaluation import run
        run(_default_set(kind))
    finally:
        try:
            LOCK.unlink()
        except OSError:
            pass


def _default_set(kind):
    if kind == "app":
        from evaluation.run_app_evaluation import DEFAULT_SET
    else:
        from evaluation.run_doc_evaluation import DEFAULT_SET
    return str(DEFAULT_SET)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
