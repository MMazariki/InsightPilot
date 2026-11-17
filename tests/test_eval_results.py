import json
import time

from src import eval_results as er


def _app_run(passed_ids):
    groups = {"A1": "wording", "A2": "wording", "A3": "targets", "A4": "decline"}
    results = []
    for case_id, group in groups.items():
        unsupported = group == "decline"
        passed = case_id in passed_ids
        results.append({
            "id": case_id, "group": group,
            "category": "unsupported" if unsupported else "answerable",
            "question": f"q {case_id}", "passed": passed, "seconds": 2.0,
            "answer_kind": "declined" if unsupported or not passed else "answer",
            "reason": "rows match" if passed else "declined: undefined_metric"})
    return {"results": results}


def _write(folder, name, data):
    (folder / name).write_text(json.dumps(data), encoding="utf-8")


def test_runs_are_listed_oldest_first(tmp_path):
    _write(tmp_path, "app_cases_v1_20261118-090000.json", _app_run({"A1"}))
    _write(tmp_path, "app_cases_v1_20261117-090000.json", _app_run({"A1", "A2"}))
    _write(tmp_path, "doc_cases_v1_20261117-090000.json", {"results": []})
    _write(tmp_path, "fresh_v11_prompt_v11_evaluation.json", {})
    runs = er.list_runs("app", tmp_path)
    assert [r["stamp"] for r in runs] == ["20261117-090000", "20261118-090000"]
    assert runs[0]["set"] == "app_cases_v1"
    assert er.stamp_label(runs[0]["stamp"]) == "2026-11-17 09:00"


def test_app_summary_and_failure_types():
    s = er.app_summary(_app_run({"A1", "A3", "A4"}))
    assert (s["passed"], s["total"], s["answerable_passed"], s["declined_passed"]) == (3, 4, 2, 1)
    assert s["groups"][0] == {"group": "Everyday wording", "passed": 1, "total": 2}
    assert s["failure_types"] == [{"type": "declined an answerable question", "count": 1}]


def test_insights_compare_with_the_previous_run(tmp_path):
    _write(tmp_path, "app_cases_v1_20261117-090000.json", _app_run({"A1", "A4"}))
    _write(tmp_path, "app_cases_v1_20261118-090000.json", _app_run({"A1", "A2", "A4"}))
    notes = er.insights("app", er.list_runs("app", tmp_path))
    assert any("+1 passed; fixed A2" in n for n in notes)
    assert any("Weakest area: Targets" in n for n in notes)


def test_doc_summary_explains_losses():
    data = {"summary": {"answerable": 2, "retrieval": 2, "citation": 1, "answer": 1,
                        "answer_and_citation": 1, "unanswerable": 1, "declined": 1},
            "results": [
                {"id": "D1", "category": "answerable", "passed": True, "retrieval": True,
                 "found": True, "cited_sources": [["x", 1]], "citation": True, "seconds": 3},
                {"id": "D2", "category": "answerable", "passed": False, "retrieval": True,
                 "found": False, "cited_sources": [], "citation": False, "seconds": 5},
                {"id": "D3", "category": "unanswerable", "passed": True, "seconds": 1}]}
    s = er.doc_summary(data)
    assert s["passed"] == 2 and s["total"] == 3
    assert s["failure_types"] == [{"type": "said 'not found' with the page in hand",
                                   "count": 1}]
    notes = er.insights("docs", [{"stamp": "20261117-090000", "set": "doc_cases_v1",
                                  "data": data}])
    assert any("bottleneck" in n for n in notes)


def test_running_job_lock(tmp_path):
    lock = tmp_path / "job.json"
    assert er.running_job(lock) is None
    lock.write_text(json.dumps({"kind": "app", "started_ts": time.time(),
                                "started": "2026-11-17T17:00:00"}))
    assert er.running_job(lock)["kind"] == "app"
    lock.write_text(json.dumps({"kind": "app", "started_ts": time.time() - 7200}))
    assert er.running_job(lock) is None  # stale lock from a crash
