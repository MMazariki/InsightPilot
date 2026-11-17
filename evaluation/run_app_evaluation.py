"""App-level evaluation: questions through the full assistant, as in the app.

    python -m evaluation.run_app_evaluation [--set evaluation/app_cases_v1.json]

Each question goes through ``StructuredPlanningAgent.ask`` with the app's
current settings (interpretation, analysis, checks), and the final answer
table (``agent.last_result``) is compared with the result of the case's
hand-written reference DAX, run read-only against the published dataset.

Comparison is by values, not column names: every value of a reference row
must be found in a different cell of the matching answer row (answers may
show extra columns), with the same number of rows. Numbers match within
0.01 (or 1e-6 relative); a percentage shown as text ("272.1%") matches a
fraction within 0.0006. ``mode``: ``rows`` = same rows in any order;
``top1`` = the first answer row matches the first reference row.
Unsupported cases pass when the app declines (no query run).

"Today" is fixed to the set's ``as_of`` date so relative dates ("this
year", "last month") are reproducible. Results are written to
``evaluation/results/<set>_<timestamp>.json`` (gitignored: business data)
and never overwrite an earlier file.
"""

import argparse
import datetime as dt
import json
import math
import re
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RESULTS = ROOT / "evaluation" / "results"
DEFAULT_SET = ROOT / "evaluation" / "app_cases_v1.json"

_PERCENT = re.compile(r"^(-?[\d,]*\.?\d+)%$")


# ------------------------------------------------------------- comparison

def normalise(value):
    """-> None | ("num", float, is_percent_text) | ("bool", b) | ("str", s)"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, (int, float)):
        return ("num", float(value), False)
    text = str(value).strip()
    match = _PERCENT.match(text)
    if match:
        return ("num", float(match.group(1).replace(",", "")) / 100, True)
    return ("str", text)


def values_match(expected, actual):
    if expected is None or actual is None:
        return expected is None and actual is None
    if expected[0] != actual[0]:
        return False
    if expected[0] == "num":
        if expected[2] or actual[2]:
            return abs(expected[1] - actual[1]) <= 0.0006
        return math.isclose(expected[1], actual[1], rel_tol=1e-6, abs_tol=0.01)
    return expected[1] == actual[1]


def row_matches(expected_row, actual_row):
    """Every expected value in a different actual cell (backtracking)."""
    expected = [normalise(v) for v in expected_row.values()]
    actual = [normalise(v) for v in actual_row.values()]
    # Blank expected cells only need a blank somewhere; check values first.
    order = sorted(range(len(expected)), key=lambda i: expected[i] is None)
    used = [False] * len(actual)

    def place(k):
        if k == len(order):
            return True
        value = expected[order[k]]
        for j, candidate in enumerate(actual):
            if not used[j] and values_match(value, candidate):
                used[j] = True
                if place(k + 1):
                    return True
                used[j] = False
        return False

    return place(0)


def compare(reference_rows, answer_rows, mode):
    if mode == "top1":
        if not answer_rows or not reference_rows:
            return False, "no rows"
        return (row_matches(reference_rows[0], answer_rows[0]),
                "first row" + ("" if row_matches(reference_rows[0], answer_rows[0])
                               else " differs"))
    if len(reference_rows) != len(answer_rows):
        return False, f"row count {len(answer_rows)} != {len(reference_rows)}"
    remaining = list(answer_rows)
    for ref in reference_rows:
        index = next((i for i, row in enumerate(remaining) if row_matches(ref, row)), None)
        if index is None:
            return False, f"no answer row matches reference row {ref}"
        remaining.pop(index)
    return True, "rows match"


# --------------------------------------------------------------- running

def _fix_today(as_of):
    import src.analysis as analysis
    import src.interpret as interpret

    day = dt.date.fromisoformat(as_of)

    class FixedDate(dt.date):
        @classmethod
        def today(cls):
            return day

    shim = types.SimpleNamespace(date=FixedDate, timedelta=dt.timedelta)
    interpret.dt = shim
    analysis.dt = shim


def run(set_path):
    from src import agent_factory
    from src import settings as app_settings
    from src.structured_agent import StructuredPlanningAgent

    case_set = json.loads(Path(set_path).read_text(encoding="utf-8"))
    _fix_today(case_set["as_of"])
    settings = app_settings.load()
    agent = StructuredPlanningAgent(**agent_factory.structured_agent_kwargs(settings))

    results = []
    for case in case_set["cases"]:
        started = time.time()
        try:
            agent.ask(case["question"])
            answer = agent.last_result or {"kind": "failed"}
        except Exception as exc:  # recorded, never stops the run
            answer = {"kind": "exception", "error": f"{type(exc).__name__}: {exc}"}
        trace = agent.last_trace or {}
        record = {"id": case["id"], "group": case["group"],
                  "category": case["category"], "question": case["question"],
                  "answer_kind": answer["kind"], "stages": trace.get("stages"),
                  "interpreted": trace.get("interpreted"),
                  "seconds": round(time.time() - started, 1)}
        if case["category"] == "unsupported":
            record["passed"] = answer["kind"] == "declined"
            record["reason"] = answer.get("reason") or answer["kind"]
        elif answer["kind"] != "answer":
            record["passed"] = False
            record["reason"] = f"{answer['kind']}: {answer.get('reason') or answer.get('error', '')}"[:300]
        else:
            reference = agent.powerbi.execute_dax(case["reference_dax"])
            passed, reason = compare(reference["rows"], answer["rows"], case["mode"])
            record.update(passed=passed, reason=reason,
                          answer_columns=answer["columns"],
                          answer_rows=answer["rows"][:20],
                          reference_rows=reference["rows"][:20],
                          dax=answer.get("dax"))
        results.append(record)
        print(f"{record['id']} {'PASS' if record['passed'] else 'FAIL'} "
              f"[{record['group']}] {case['question']} -> {record['reason'][:110]}",
              flush=True)

    summary = {}
    for record in results:
        group = summary.setdefault(record["group"], {"passed": 0, "total": 0})
        group["total"] += 1
        group["passed"] += int(record["passed"])
    summary["all"] = {"passed": sum(r["passed"] for r in results),
                      "total": len(results)}

    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = RESULTS / f"{Path(set_path).stem}_{stamp}.json"
    if out.exists():
        raise SystemExit(f"{out} exists; not overwritten.")
    out.write_text(json.dumps({
        "set": str(Path(set_path).relative_to(ROOT)),
        "as_of": case_set["as_of"],
        "run_at": dt.datetime.now().isoformat(timespec="seconds"),
        "planner_model": settings["planner"]["model"],
        "measured_defaults": app_settings.is_default(settings),
        "pipeline_options": settings["pipeline"],
        "summary": summary,
        "results": results,
    }, indent=2, default=str), encoding="utf-8")
    print("\n" + json.dumps(summary, indent=1))
    print(f"Written: {out.relative_to(ROOT)}")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--set", default=str(DEFAULT_SET))
    run(parser.parse_args().set)


if __name__ == "__main__":
    main()
