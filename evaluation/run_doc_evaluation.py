"""Documents-page evaluation on the project-documents corpus.

    python -m evaluation.run_doc_evaluation [--set evaluation/doc_cases_v1.json]

Builds the corpus (evaluation/doc_corpus.py) in a temporary folder with
its own index (the user's documents folder and index are not touched),
then asks every question through ``DocumentService.ask`` with the app's
current Documents settings and scores:

* retrieval: an acceptable file/page is among the passages given to the
  answer model;
* citation: the answer cites an acceptable file/page;
* answer: the answer contains a word from every required word group;
* unanswerable: the not-found reply.

Results go to evaluation/results/<set>_<timestamp>.json (never
overwritten).
"""

import argparse
import datetime as dt
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RESULTS = ROOT / "evaluation" / "results"
DEFAULT_SET = ROOT / "evaluation" / "doc_cases_v1.json"


def acceptable(hit, sources):
    name = hit["file_key"].split("/")[-1]
    for file_name, pages in sources:
        if name == file_name and any(hit["page_start"] <= p <= hit["page_end"]
                                     for p in pages):
            return True
    return False


def has_words(text, groups):
    lower = text.lower()
    return all(any(word.lower() in lower for word in group) for group in groups)


def run(set_path):
    from evaluation.doc_corpus import build
    from src import settings as app_settings
    from src.rag.service import DocumentService

    case_set = json.loads(Path(set_path).read_text(encoding="utf-8"))
    folder_id = case_set["folder"]
    settings = app_settings.load()
    work = Path(tempfile.mkdtemp(prefix="insightpilot_doc_eval_"))
    try:
        build(work / "docs" / folder_id)
        config = dict(settings["documents"], folder=str(work / "docs"))
        service = DocumentService(config, index_dir=work / "index")
        started = time.time()
        summary_index = service.index()
        index_seconds = round(time.time() - started, 1)
        c = service.config
        allowed = service.folder_keys(folder_id)

        results = []
        for case in case_set["cases"]:
            started = time.time()
            hits, best = service.retriever.search(
                case["question"], c["top_k"], c["min_score"], allowed,
                c["candidates"], c["keyword_search"], c["fusion_k"])
            answer = service.ask(case["question"], folder_id=folder_id)
            record = {"id": case["id"], "category": case["category"],
                      "question": case["question"], "answer": answer["text"],
                      "found": answer["found"], "cited": answer["cited"],
                      "best_score": round(best, 3),
                      "retrieved": [(h["file_key"], h["page_start"]) for h in hits],
                      "cited_sources": [(s["file_key"], s["page_start"])
                                        for s in answer["sources"]],
                      "seconds": round(time.time() - started, 1)}
            if case["category"] == "unanswerable":
                record["passed"] = not answer["found"]
            else:
                record["retrieval"] = any(acceptable(h, case["sources"]) for h in hits)
                record["citation"] = any(acceptable(s, case["sources"])
                                         for s in answer["sources"])
                record["answer_ok"] = answer["found"] and has_words(answer["text"],
                                                                    case["words"])
                record["passed"] = record["answer_ok"] and record["citation"]
            results.append(record)
            flags = ("" if case["category"] == "unanswerable" else
                     f" retrieval={record['retrieval']} citation={record['citation']} "
                     f"answer={record['answer_ok']}")
            print(f"{case['id']} {'PASS' if record['passed'] else 'FAIL'}{flags} | "
                  f"{case['question']} -> {answer['text'][:90]!r}", flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    answerable = [r for r in results if r["category"] == "answerable"]
    unanswerable = [r for r in results if r["category"] == "unanswerable"]
    summary = {
        "answerable": len(answerable),
        "retrieval": sum(r["retrieval"] for r in answerable),
        "citation": sum(r["citation"] for r in answerable),
        "answer": sum(r["answer_ok"] for r in answerable),
        "answer_and_citation": sum(r["passed"] for r in answerable),
        "unanswerable": len(unanswerable),
        "declined": sum(r["passed"] for r in unanswerable),
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = RESULTS / f"{Path(set_path).stem}_{stamp}.json"
    if out.exists():
        raise SystemExit(f"{out} exists; not overwritten.")
    out.write_text(json.dumps({
        "set": str(Path(set_path).relative_to(ROOT)),
        "run_at": dt.datetime.now().isoformat(timespec="seconds"),
        "documents_settings": {k: v for k, v in settings["documents"].items()
                               if k != "system_prompt"},
        "index": summary_index, "index_seconds": index_seconds,
        "summary": summary, "results": results,
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
