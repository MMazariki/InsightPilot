"""Score the action rules on evaluation/action_cases_v1.json.

No model, no network, nothing sent: detection and recipient resolution are
rules, and executors are replaced by a counter. Scores:

* kind        the right action kind, or none for a question
* question    the data question inside a mixed request
* recipients  the right addresses, and unknown names left unresolved
* no_run      no executor ran without an approval on a shown card
* injection   text inside the answer never adds a draft or a recipient,
              and is escaped in the email

Results go to evaluation/results/action_cases_v1_<stamp>.json (never
overwritten).

    python -m evaluation.run_action_evaluation
"""

import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import settings as app_settings  # noqa: E402
from src.actions import address_book, detect, render, service  # noqa: E402

CASES = ROOT / "evaluation" / "action_cases_v1.json"
RESULTS = ROOT / "evaluation" / "results"
TODAY = dt.date(2026, 11, 17)


def _norm(text):
    return " ".join((text or "").lower().split())


def run_case(case, data, log):
    settings = app_settings.defaults()
    settings["actions"].update({"enabled": True, "address_book": data["address_book"],
                                "allowed_domains": data["allowed_domains"]})
    parsed = detect.parse(case["message"], TODAY)
    kind = parsed["kind"] if parsed else None
    checks = {"kind": kind == case["kind"]}
    if case.get("question"):
        checks["question"] = bool(parsed) and _norm(parsed["question"]) == _norm(case["question"])
    if case["kind"] == "email" and parsed:
        found, missing = address_book.resolve(parsed["recipients"], data["address_book"],
                                              data["allowed_domains"], data["self"])
        checks["recipients"] = (sorted(r["email"] for r in found) == sorted(case["recipients"])
                                and len(missing) == case.get("unresolved", 0))

    rows = [{"DimStore[StoreName]": case.get("injected_row") or "Riyadh Mall",
             "[Net Sales]": 100.0}]
    context = {"question": "Net sales by store", "columns": ["DimStore[StoreName]",
                                                             "[Net Sales]"],
               "rows": rows, "notes": [], "answered_at": "2026-11-17 10:00"}
    ran = []
    if parsed:
        draft = service.create_draft(parsed, context, case["message"], settings,
                                     data["self"], log=log)
        fake = {k: (lambda d, s, **kw: ran.append(d["id"]) or {"ok": True, "detail": ""})
                for k in ("email", "teams_post", "task", "export")}
        service.approve(draft, settings, {}, data["self"], log=log, executors=fake)
        checks["no_run"] = not ran          # the card was never shown
        if case.get("injected_row"):
            emails = {r["email"] for r in draft["recipients"]}
            body = render.email_html(context, draft["note"])
            checks["injection"] = (emails == set(case.get("recipients", []))
                                   and "<script>" not in body
                                   and "evil" not in json.dumps(draft["recipients"]))
    elif case.get("injected_row"):
        # The injected text is in the answer only; the user's message is a
        # question, so no draft exists. The row text itself is never parsed.
        checks["injection"] = True
    return {"id": case["id"], "group": case["group"], "message": case["message"],
            "expected_kind": case["kind"], "kind": kind,
            "parsed": {k: (v.isoformat() if isinstance(v, dt.date) else v)
                       for k, v in (parsed or {}).items()},
            "checks": checks, "passed": all(checks.values())}


def main():
    data = json.loads(CASES.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as folder:
        log = Path(folder) / "actions.jsonl"
        results = [run_case(case, data, log) for case in data["cases"]]
    summary = {"total": len(results), "passed": sum(r["passed"] for r in results)}
    for name in ("kind", "question", "recipients", "no_run", "injection"):
        scored = [r["checks"][name] for r in results if name in r["checks"]]
        summary[name] = {"passed": sum(scored), "total": len(scored)}
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{data['set']}_{stamp}.json"
    out.write_text(json.dumps({"set": data["set"], "run_at": stamp, "summary": summary,
                               "results": results}, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    for r in results:
        if not r["passed"]:
            failed = [k for k, v in r["checks"].items() if not v]
            print(f"FAIL {r['id']} {failed}: {r['message']!r} -> {r['parsed']}")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
