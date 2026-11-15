from evaluation.result_comparator import compare_results


def score_evaluation(reference_cases, candidate_cases, comparator=compare_results):
    """Score candidate Power BI results against reference results."""

    ids = [case["id"] for case in candidate_cases]

    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate candidate evaluation IDs.")

    candidates = {
        case["id"]: case
        for case in candidate_cases
    }

    scores = []

    for reference in reference_cases:
        case_id = reference["id"]
        candidate = candidates.get(case_id)

        if reference["status"] != "success":
            scores.append({
                "id": case_id,
                "status": "reference_unavailable",
                "passed": False,
            })
            continue

        if candidate is None:
            scores.append({
                "id": case_id,
                "status": "missing_candidate",
                "passed": False,
            })
            continue

        if candidate.get("status") != "success":
            scores.append({
                "id": case_id,
                "status": "candidate_failed",
                "passed": False,
            })
            continue

        comparison = comparator(reference, candidate)

        scores.append({
            "id": case_id,
            "status": "evaluated",
            "passed": comparison["passed"],
            "reason": comparison["reason"],
        })

    eligible = [
        item for item in scores
        if item["status"] != "reference_unavailable"
    ]

    passed = sum(item["passed"] for item in eligible)

    return {
        "total": len(eligible),
        "passed": passed,
        "failed": len(eligible) - passed,
        "accuracy": (
            passed / len(eligible)
            if eligible else None
        ),
        "cases": scores,
    }

