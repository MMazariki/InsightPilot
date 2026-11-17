"""Evaluation dashboard: statistics and insights from the test runs.

Opened with the Evaluation button in the main header or ``?view=evaluation``.
Reads the result files in evaluation/results/ (src/eval_results.py) and can
rerun either evaluation in the background (evaluation/run_job.py).
"""

import subprocess
import sys
import time
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from src import eval_results as er

ROOT = Path(__file__).resolve().parents[1]

# Reference palette (dataviz skill): one series hue, status pair for pass/fail.
SERIES = "#2a78d6"
GOOD = "#0ca30c"
CRITICAL = "#d03b3b"
MUTED = "#52514e"
GRID = "#e8e7e3"


# ------------------------------------------------------------------ jobs

def _start(kind):
    log = ROOT / "logs" / f"evaluation_{kind}.log"
    log.parent.mkdir(exist_ok=True)
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    with open(log, "w", encoding="utf-8") as handle:
        subprocess.Popen([sys.executable, "-m", "evaluation.run_job", kind],
                         cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT,
                         creationflags=flags)
    st.session_state.eval_starting = {"kind": kind, "at": time.time()}


def _log_tail(kind, lines=6):
    path = ROOT / "logs" / f"evaluation_{kind}.log"
    try:
        text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [], 0
    return [line for line in text if "PASS" in line or "FAIL" in line][-lines:], \
        sum(1 for line in text if " PASS " in line or " FAIL " in line)


# ---------------------------------------------------------------- charts

def _axis(title=None, fmt=None):
    return alt.Axis(title=title, format=fmt, grid=True, gridColor=GRID,
                    labelColor=MUTED, titleColor=MUTED, domain=False, ticks=False)


def _bars(frame, label, value_title):
    """Horizontal bars of pass rate (0-100%) with the count as a label."""
    frame = frame.assign(rate=frame["passed"] / frame["total"],
                         text=[f"{p}/{t} · {round(100 * p / t)}%"
                               for p, t in zip(frame["passed"], frame["total"])])
    base = alt.Chart(frame).encode(
        y=alt.Y(f"{label}:N", sort=None, title=None,
                axis=alt.Axis(labelColor="#0b0b0b", labelLimit=220, domain=False,
                              ticks=False)),
        x=alt.X("rate:Q", scale=alt.Scale(domain=[0, 1]),
                axis=_axis(value_title, "%")),
        tooltip=[alt.Tooltip(f"{label}:N", title=label.title()),
                 alt.Tooltip("passed:Q", title="Passed"),
                 alt.Tooltip("total:Q", title="Questions"),
                 alt.Tooltip("rate:Q", title="Pass rate", format=".0%")])
    bars = base.mark_bar(color=SERIES, cornerRadiusEnd=4, height=18)
    text = base.mark_text(align="left", dx=6, color="#0b0b0b", fontSize=12).encode(
        text="text:N")
    return (bars + text).properties(height=46 * len(frame) + 20).configure_view(
        strokeWidth=0)


def _trend(runs, kind):
    rows = []
    for run in runs:
        s = er.summary(kind, run["data"])
        rows.append({"run": er.stamp_label(run["stamp"]), "set": run["set"],
                     "rate": s["passed"] / s["total"],
                     "label": f"{s['passed']}/{s['total']}"})
    frame = pd.DataFrame(rows)
    base = alt.Chart(frame).encode(
        x=alt.X("run:N", sort=None, title=None, axis=alt.Axis(labelAngle=0,
                                                               labelColor=MUTED)),
        y=alt.Y("rate:Q", scale=alt.Scale(domain=[0, 1]), axis=_axis("Passed", "%")),
        tooltip=[alt.Tooltip("run:N", title="Run"), alt.Tooltip("set:N", title="Set"),
                 alt.Tooltip("label:N", title="Passed"),
                 alt.Tooltip("rate:Q", title="Pass rate", format=".0%")])
    line = base.mark_line(color=SERIES, strokeWidth=2)
    points = base.mark_point(color=SERIES, filled=True, size=80)
    return (line + points).properties(height=200).configure_view(strokeWidth=0)


# ----------------------------------------------------------------- tiles

def _tiles(kind, runs):
    latest = er.summary(kind, runs[-1]["data"])
    previous = er.summary(kind, runs[-2]["data"]) if len(runs) > 1 else None

    def delta(key):
        if previous is None or runs[-1]["set"] != runs[-2]["set"]:
            return None
        return latest[key] - previous[key]

    pct = round(100 * latest["passed"] / latest["total"])
    a, b, c, d = st.columns(4)
    a.metric("Passed", f"{latest['passed']} / {latest['total']}",
             delta=delta("passed"), help=f"{pct}% of all questions")
    if kind == "app":
        b.metric("Answerable correct",
                 f"{latest['answerable_passed']} / {latest['answerable']}",
                 delta=delta("answerable_passed"))
    else:
        both = latest["steps"][-1]
        b.metric("Answer + citation", f"{both['passed']} / {both['total']}")
    c.metric("Correctly declined", f"{latest['declined_passed']} / {latest['unsupported']}",
             delta=delta("declined_passed"))
    d.metric("Seconds per question", latest["avg_seconds"])


# ---------------------------------------------------------------- tables

def _case_rows(kind, data, failed_only):
    rows = []
    for r in data["results"]:
        if failed_only and r["passed"]:
            continue
        if kind == "app":
            why = er.failure_type(r) or ""
            detail = r.get("reason", "")
            rows.append({"Result": "✅ Pass" if r["passed"] else "❌ Fail",
                         "Case": r["id"], "Group": er.GROUP_LABELS.get(r["group"], r["group"]),
                         "Question": r["question"], "Why": why,
                         "Detail": detail[:160], "Seconds": r.get("seconds")})
        else:
            checks = ("" if r["category"] == "unanswerable" else
                      " · ".join(f"{name} {'✓' if r.get(key) else '✗'}"
                                 for key, name in (("retrieval", "found"),
                                                   ("answer_ok", "answer"),
                                                   ("citation", "cited"))))
            rows.append({"Result": "✅ Pass" if r["passed"] else "❌ Fail",
                         "Case": r["id"], "Question": r["question"],
                         "Checks": checks, "Answer": r.get("answer", "")[:160],
                         "Seconds": r.get("seconds")})
    return rows


# ------------------------------------------------------------------ page

def _section(kind, job):
    info = er.KINDS[kind]
    runs = er.list_runs(kind)
    head, button = st.columns([4, 1.2], vertical_alignment="center")
    with head:
        st.html(f'<div class="ip-card-title">{info["label"]}</div>'
                '<div class="ip-card-sub">'
                + (f"Latest run {er.stamp_label(runs[-1]['stamp'])} · set "
                   f"<code>{runs[-1]['set']}</code> · {len(runs)} run(s)"
                   if runs else "No runs yet") + "</div>")
    with button:
        busy = job is not None
        if st.button("Rerun evaluation", key=f"rerun_{kind}", icon=":material/replay:",
                     type="primary", disabled=busy, use_container_width=True,
                     help="Runs every question again with the current settings "
                          "(local models; takes several minutes). The result is "
                          "added as a new run."):
            _start(kind)
            st.rerun()
    if not runs:
        st.info("No results yet. Click Rerun evaluation.", icon=":material/science:")
        return

    data = runs[-1]["data"]
    _tiles(kind, runs)

    left, right = st.columns([3, 2], gap="large")
    with left:
        if kind == "app":
            frame = pd.DataFrame(er.app_summary(data)["groups"])
            st.markdown("**Pass rate by question group**")
            st.altair_chart(_bars(frame, "group", "Passed"), use_container_width=True)
        else:
            frame = pd.DataFrame(er.doc_summary(data)["steps"])
            st.markdown("**From search to cited answer** (answerable questions)")
            st.altair_chart(_bars(frame, "step", "Questions"), use_container_width=True)
    with right:
        st.markdown("**Insights**")
        for note in er.insights(kind, runs):
            st.markdown(f"- {note}")

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown("**Pass rate per run**")
        if len(runs) > 1:
            st.altair_chart(_trend(runs, kind), use_container_width=True)
        else:
            st.caption("The trend appears after the next run.")
    with right:
        st.markdown("**Why questions failed**")
        types = er.summary(kind, data)["failure_types"]
        if types:
            st.dataframe(pd.DataFrame(types).rename(
                columns={"type": "Failure", "count": "Cases"}),
                hide_index=True, use_container_width=True)
        else:
            st.success("No failures in the latest run.", icon=":material/check_circle:")

    failures = _case_rows(kind, data, failed_only=True)
    if failures:
        st.markdown("**Failed questions**")
        st.dataframe(pd.DataFrame(failures), hide_index=True, use_container_width=True)
    with st.expander(f"All {len(data['results'])} questions in the latest run"):
        st.dataframe(pd.DataFrame(_case_rows(kind, data, failed_only=False)),
                     hide_index=True, use_container_width=True, height=420)


@st.fragment(run_every=5)
def _job_status():
    job = er.running_job()
    starting = st.session_state.get("eval_starting")
    if job is None and starting and time.time() - starting["at"] < 20:
        st.info(f"Starting the {er.KINDS[starting['kind']]['label']} evaluation...",
                icon=":material/hourglass_top:")
        return
    if job is None:
        if st.session_state.pop("eval_was_running", False):
            st.session_state.pop("eval_starting", None)
            st.rerun()  # refresh the whole page with the new results
        return
    st.session_state.eval_was_running = True
    lines, done = _log_tail(job["kind"])
    total = (33 if job["kind"] == "app" else 25)
    st.info(f"Running the {er.KINDS[job['kind']]['label']} evaluation (started "
            f"{job['started'][11:16]}): {done} question(s) done. The page updates "
            "by itself.", icon=":material/sync:")
    st.progress(min(done / total, 1.0))
    if lines:
        st.code("\n".join(line[:150] for line in lines), language=None)


def render():
    title, back = st.columns([5, 1.3], vertical_alignment="center")
    with title:
        st.html('<div class="ip-card-title" style="font-size:1.25rem">Evaluation '
                'dashboard</div><div class="ip-card-sub">How often the assistants '
                'answer correctly, measured on fixed question sets against the live '
                'data. Methods and caveats: docs/EVALUATION.md.</div>')
    with back:
        if st.button("Back to Power BI", key="eval_back", icon=":material/arrow_back:",
                     use_container_width=True):
            st.session_state.view = "chat"
            st.query_params.clear()
            st.rerun()

    _job_status()
    job = er.running_job()
    starting = st.session_state.get("eval_starting")
    if job is None and starting and time.time() - starting["at"] < 20:
        job = starting

    for kind in ("app", "docs"):
        with st.container(border=True, key=f"eval_card_{kind}"):
            _section(kind, job)

    st.caption("Question sets are small and written by the developer; each point "
               "is one run. A rerun uses the current Admin settings, needs Ollama "
               "and, for the Power BI set, the saved Power BI sign-in. Result files: "
               "evaluation/results/ (not committed).")
