"""Evaluation results for the dashboard: load runs, summarise, explain.

Result files are written by evaluation/run_app_evaluation.py and
evaluation/run_doc_evaluation.py into evaluation/results/ (gitignored) as
``<set>_<YYYYmmdd-HHMMSS>.json``. Nothing here runs a model.
"""

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evaluation" / "results"

KINDS = {
    "app": {"prefix": "app_cases_", "label": "Power BI assistant",
            "module": "evaluation.run_app_evaluation"},
    "docs": {"prefix": "doc_cases_", "label": "Documents page",
             "module": "evaluation.run_doc_evaluation"},
}
GROUP_LABELS = {
    "wording": "Everyday wording",
    "analysis": "Growth, share, periods",
    "targets": "Targets",
    "decline": "Should decline",
}
_STAMP = re.compile(r"_(\d{8}-\d{6})\.json$")


def list_runs(kind, folder=RESULTS):
    """Runs of one kind, oldest first: ``[{"path", "stamp", "set", "data"}]``."""
    prefix = KINDS[kind]["prefix"]
    runs = []
    for path in sorted(Path(folder).glob(f"{prefix}*.json")):
        match = _STAMP.search(path.name)
        if not match:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        runs.append({"path": path, "stamp": match.group(1),
                     "set": path.name[:match.start()], "data": data})
    return sorted(runs, key=lambda r: r["stamp"])


def stamp_label(stamp):
    return f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]} {stamp[9:11]}:{stamp[11:13]}"


# ----------------------------------------------------------------- app

def app_summary(data):
    results = data["results"]
    answerable = [r for r in results if r["category"] == "answerable"]
    unsupported = [r for r in results if r["category"] == "unsupported"]
    groups = []
    for key in GROUP_LABELS:
        rows = [r for r in results if r["group"] == key]
        if rows:
            groups.append({"group": GROUP_LABELS[key], "passed": sum(r["passed"] for r in rows),
                           "total": len(rows)})
    for key in sorted({r["group"] for r in results} - set(GROUP_LABELS)):
        rows = [r for r in results if r["group"] == key]
        groups.append({"group": key, "passed": sum(r["passed"] for r in rows),
                       "total": len(rows)})
    seconds = [r.get("seconds") or 0 for r in results]
    return {
        "passed": sum(r["passed"] for r in results), "total": len(results),
        "answerable_passed": sum(r["passed"] for r in answerable),
        "answerable": len(answerable),
        "declined_passed": sum(r["passed"] for r in unsupported),
        "unsupported": len(unsupported),
        "groups": groups,
        "avg_seconds": round(sum(seconds) / len(seconds), 1) if seconds else 0,
        "failure_types": failure_types(results),
    }


def failure_type(record):
    """A plain-words category for one failed case."""
    if record["passed"]:
        return None
    if record["category"] == "unsupported":
        return "answered a question it should decline"
    kind = record.get("answer_kind")
    if kind == "declined":
        return "declined an answerable question"
    if kind in ("failed", "exception"):
        return "no valid query"
    if kind == "error":
        return "Power BI error"
    reason = record.get("reason", "")
    if reason.startswith("row count"):
        return "wrong number of rows"
    if "first row" in reason:
        return "wrong top result"
    return "wrong values"


def failure_types(results):
    counts = Counter(t for t in (failure_type(r) for r in results) if t)
    return [{"type": t, "count": n} for t, n in counts.most_common()]


# ---------------------------------------------------------------- docs

DOC_STEPS = [("retrieval", "Right page found"), ("answer", "Correct answer"),
             ("citation", "Right page cited"), ("answer_and_citation", "Answer + citation")]


def doc_summary(data):
    s = data["summary"]
    results = data["results"]
    seconds = [r.get("seconds") or 0 for r in results]
    answerable = [r for r in results if r["category"] == "answerable"]
    causes = Counter()
    for r in answerable:
        if r["passed"]:
            continue
        if not r.get("retrieval"):
            causes["right page not found"] += 1
        elif not r.get("found"):
            causes["said 'not found' with the page in hand"] += 1
        elif not r.get("cited_sources"):
            causes["citation missing or unreadable"] += 1
        elif not r.get("citation"):
            causes["cited the wrong passage"] += 1
        else:
            causes["answer incomplete or worded differently"] += 1
    return {
        "passed": s["answer_and_citation"] + s["declined"],
        "total": s["answerable"] + s["unanswerable"],
        "answerable": s["answerable"],
        "steps": [{"step": label, "passed": s[key], "total": s["answerable"]}
                  for key, label in DOC_STEPS],
        "declined_passed": s["declined"], "unsupported": s["unanswerable"],
        "avg_seconds": round(sum(seconds) / len(seconds), 1) if seconds else 0,
        "failure_types": [{"type": t, "count": n} for t, n in causes.most_common()],
    }


def summary(kind, data):
    return app_summary(data) if kind == "app" else doc_summary(data)


# ------------------------------------------------------------- insights

def changes(previous, latest):
    """Cases fixed and broken between two runs of the same set."""
    before = {r["id"]: r["passed"] for r in previous["results"]}
    fixed, broken = [], []
    for r in latest["results"]:
        if r["id"] not in before:
            continue
        if r["passed"] and not before[r["id"]]:
            fixed.append(r["id"])
        elif not r["passed"] and before[r["id"]]:
            broken.append(r["id"])
    return fixed, broken


def _pct(passed, total):
    return round(100 * passed / total) if total else 0


def insights(kind, runs):
    """Plain-language findings from the latest run (and the one before)."""
    if not runs:
        return []
    latest = runs[-1]["data"]
    s = summary(kind, latest)
    notes = []
    if kind == "app":
        notes.append(f"{s['passed']} of {s['total']} questions passed "
                     f"({_pct(s['passed'], s['total'])}%): {s['answerable_passed']} of "
                     f"{s['answerable']} answerable, {s['declined_passed']} of "
                     f"{s['unsupported']} correctly declined.")
        weakest = min(s["groups"], key=lambda g: g["passed"] / g["total"])
        strongest = [g["group"] for g in s["groups"] if g["passed"] == g["total"]]
        if weakest["passed"] < weakest["total"]:
            notes.append(f"Weakest area: {weakest['group']} "
                         f"({weakest['passed']}/{weakest['total']}).")
        if strongest:
            notes.append("No failures in: " + ", ".join(strongest) + ".")
        if s["declined_passed"] == s["unsupported"]:
            notes.append("Every question that should be declined was declined: no "
                         "confident answer to an unanswerable question.")
    else:
        steps = {x["step"]: x for x in s["steps"]}
        retrieval = steps["Right page found"]
        both = steps["Answer + citation"]
        notes.append(f"Search found the right page for {retrieval['passed']} of "
                     f"{retrieval['total']} questions; {both['passed']} were answered "
                     "correctly with the right citation.")
        lost = retrieval["passed"] - both["passed"]
        if lost > 0:
            notes.append(f"{lost} question(s) were lost after search, in the answer "
                         "model: the bottleneck is answering and citing, not finding.")
        notes.append(f"{s['declined_passed']} of {s['unsupported']} off-topic questions "
                     "were declined.")
    if s["failure_types"]:
        top = s["failure_types"][0]
        notes.append(f"Most common failure: {top['type']} ({top['count']}).")
    slow = sorted(latest["results"], key=lambda r: r.get("seconds") or 0)[-1]
    notes.append(f"Average {s['avg_seconds']} s per question; slowest "
                 f"{slow.get('seconds')} s ({slow['id']}).")
    if len(runs) > 1:
        previous = runs[-2]["data"]
        p = summary(kind, previous)
        delta = s["passed"] - p["passed"]
        fixed, broken = changes(previous, latest)
        if runs[-1]["set"] == runs[-2]["set"] and not (delta or fixed or broken):
            notes.append("Since the previous run: no change, the same questions "
                         "passed (the results are reproducible at temperature 0).")
        elif runs[-1]["set"] == runs[-2]["set"]:
            notes.append(f"Since the previous run: {delta:+d} passed"
                         + (f"; fixed {', '.join(fixed)}" if fixed else "")
                         + (f"; newly failing {', '.join(broken)}" if broken else "")
                         + ".")
        else:
            notes.append("The previous run used a different question set, so the "
                         "scores are not directly comparable.")
    notes.append("Small developer-written sets and one run per point: treat "
                 "differences of one or two questions as noise.")
    return notes


# -------------------------------------------------------------- reruns

LOCK = RESULTS / ".evaluation_job.json"


def running_job(lock=LOCK, stale_seconds=3600):
    """The job in progress (``{"kind", "started", "log"}``) or ``None``. A
    lock older than an hour is treated as left over from a crash."""
    import time

    try:
        data = json.loads(Path(lock).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if time.time() - data.get("started_ts", 0) > stale_seconds:
        return None
    return data
