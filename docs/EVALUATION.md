# InsightPilot — Structured Planning Evaluation

Last updated: 2026-11-16. All numbers below were measured by executing
compiled DAX read-only against the Power BI semantic model and comparing with
captured reference results. Models ran locally via Ollama
(`qwen2.5-coder:3b`, `qwen2.5-coder:7b`, Q4_K_M, temperature 0,
`num_ctx` 8192). No Azure AI Foundry or paid API was used.

Result files live in `evaluation/results/` and are gitignored (they contain
business data). This document contains scores and failure categories only.

## 1. Pipeline

```
question
  -> request guard (write/destructive requests -> clarification, no model call)
  -> Ollama planner (JSON query plan OR {"clarification": ...})
  -> alias normalisation (display labels only, recorded)            [v6]
  -> semantic checks (actionable messages)                           [v4+]
       rule 0  fix-it hints for misplaced references                 [v6]
       rule 1  text filter values must exist in the value catalog
       rule 2  no dimension distinct counts in another table's context
       rule 3  no grouping by a column filtered to a single value    [v5+]
  -> schema + model-reference validation (query_plan_validator)
  -> on rejection: ONE repair turn showing the message                [v4+]
  -> deterministic DAX compiler (no model-written DAX is executed)
  -> Power BI ExecuteQueries (read-only)
```

Requirement coverage (`evaluation/requirement_coverage.py`) compares a plan
with an explicit requirement spec (metrics by semantics, groupings, filters,
thresholds, ordering, limits, unsupported intent). In evaluation it is used
for scoring; it is not yet wired into the live app.

| Pipeline | Contents |
|---|---|
| v1 | Prompt v1, plan only |
| v2 | + clarification channel, request guard |
| v3 | + model conventions in prompt (percent scale, monthly = YearMonth, fact-key distinct counts, no unrequested groupings) |
| v4 | + value catalog in prompt, semantic rules 1–2, one repair turn |
| v5 | + semantic rule 3 (prompt text identical to v4) |
| v6 | + rule 0 fix-it hints, repair wording that discourages declining fixable mistakes, alias normalisation (prompt text identical to v4) |
| v7 | + rule 0b expression-kind hints (e.g. `sum` over a measure name → use the measure) |
| v8 | + decline path appended to the column-as-measure hint (rejected: regressed on fresh_v8) |
| v9 | v7 + answerability gate: out-of-vocabulary question words trigger a verifier that checks the plan answers the question |
| v10 | v9 + drop metrics that merely echo a grouping column (semantics-preserving normalisation) |
| v11 | v10 + the gate's verifier also runs when the question names no metric (prompt and parsing identical to v10); **used by the Streamlit app** |
| v12 | v11 with a narrower metric-word list: grouping entities (store, channel, region, ...) no longer count as naming a metric (rejected: over-refused answerable questions on fresh_v11) |

## 2. Method

* **Fresh, pre-registered sets.** Each fresh set (questions, requirement
  specs, hand-written reference DAX) was committed *before* any reference
  capture or model generation on it, and after the pipeline change it tests
  was committed. Commit order is the provenance record (see §5).
* **Same-case A/B.** Each change is compared with its predecessor on the
  same unseen set. Numbers are comparable within a set, not across sets.
* **Execution score.** Valid plan → compiled → executed → result equivalent
  to the reference (row-order-insensitive; row order is also checked when the
  requirement specifies ordering).
* **Strict score.** Execution pass **and** full requirement coverage. This
  catches plans that return the right numbers by coincidence (e.g. a
  dimension-key distinct count that happens to equal the fact-key count, or
  `>=` vs `>` on an empty result).
* **Unsupported cases.** *Not answered* = no valid plan; *declined* = a
  deliberate clarification (planner or guard). *Over-refusal* = a
  clarification on an answerable question.
* **Comparators.** HOLD and fresh_v1/v2 use a comparator tolerating one
  renamed metric column; fresh_v3+ tolerate up to four (values strict). The
  comparator is fixed per set.
* **Repair turns** only fire on rejected plans; valid plans are never sent
  back. No infrastructure retries were needed in any fresh run.

## 3. Results

### 3.1 Original 12-case holdout (HOLD-001..012)

| Architecture | 3B | 7B |
|---|---:|---:|
| Direct DAX generation, prompt V2 | 4/12 | 7/12 |
| Structured plan → compiler (pipeline v1) | **9/12** | **9/12** |

Execution score. The same 12 cases guided early development, so this is not
an unbiased estimate.

### 3.2 Fresh sets (answerable = execution / strict)

| Set | Arm | 3B answerable | 7B answerable | 3B unsupported declined | 7B unsupported declined | Over-refusals 3B / 7B |
|---|---|---:|---:|---:|---:|---:|
| fresh_v1 (13 + 4) | v1 | 7 / 7 | 7 / 6 | 0 (2 not answered) | 0 (1 not answered) | – |
| fresh_v2 (12 + 10) | v1 | 8 / 8 | 10 / 8 | 0 | 0 | 0 / 0 |
| | **v2** | 8 / 8 | 10 / 8 | **4** | **7** | 0 / 0 |
| fresh_v3 (16 + 6) | v2 | 9 / 7 | 7 / 5 | 3 | 4 | 0 / 0 |
| | **v3** | **12 / 11** | **13 / 10** | 3 | 4 | 0 / 0 |
| fresh_v4 (16 + 4) | v3 | 12 / 11 | 10 / 5 | 1 | 3 | 0 / 0 |
| | **v4** | 13 / 12 | 9 / **7** | 3 | 4 | 0 / 1 |
| fresh_v5 (16 + 4) | v4 | 11 / 11 | 9 / 8 | 3 | 4 | 4 / 0 |
| | **v5** | 12 / 12 | **14 / 13** | 3 | 4 | 3 / 0 |
| fresh_v6 (14 + 6) | v5 | 10 / 10 | 11 / 10 | 6 | 5 | 1 / 1 |
| | **v6** | 10 / 10 | 12 / **11** | 6 | 5 | 3 / 0 |
| fresh_v7 (14 + 6) | v6 | 12 / 11 | 10 / 9 | 5 | 4 | 2 / 3 |
| | **v7** | 12 / 11 | 10 / 9 | 5 | 4 | 2 / 2 |
| fresh_v8 (12 + 8) | v7 | 8 / 8 | 11 / 11 | 6 | 5 | 4 / 0 |
| | v8 | 8 / 8 | 10 / 10 | 6 | 5 | 4 / 1 |
| fresh_v9 (12 + 8) | v7 | 5 / 5 | 12 / 11 | 7 | 5 | 7 / 0 |
| | **v9** | 4 / 4 | 12 / 11 | 8 | **7** | 8 / 0 |
| fresh_v10 (12 + 8) | v10 | 5 / 5 | 8 / 7 | 5 | 3 | 6 / 0 |
| | **v11** | 5 / 5 | 8 / 7 | **6** | **5** | 6 / 0 |
| fresh_v11 (12 + 8) | **v11** | 8 / 8 | 10 / 8 | 5 | 0 | 2 / 1 |
| | v12 | 7 / 7 | 8 / 6 | 7 | 6 | 4 / 3 |

Set sizes are (answerable incl. one empty-result case + unsupported).
fresh_v7: FRESH7-012 unexpectedly returns no rows (no month exceeds the
threshold); it was kept unchanged after reference capture.
fresh_v2 3B: one execution failure is a single-alias comparator false
negative (both metrics renamed; values verified identical).

### 3.3 What each change did

* **Clarification channel + guard (v2).** Declines on unsupported requests
  rose from 0 to 4/10 (3B) and 7/10 (7B) with no over-refusals. Without it,
  "Delete all sales transactions from 2025" was silently planned as a read
  query with `Year <> 2025`.
* **Model conventions (v3).** Fixed percentage scale ("45%" planned as 45)
  and monthly grouping (MonthName instead of YearMonth) on most probes; gains
  on untargeted questions were small (+0 3B, +1 7B).
* **Value catalog + rules 1–2 + repair (v4).** All filter values matched the
  catalog (v3 used e.g. "Click and Collect" for "Click & Collect"). 7B
  dimension-key distinct counts went 0/3 → 3/3 strict, all via repair.
  Exposed the next dominant failure: grouping by the filtered column.
* **Rule 3 (v5).** 7B single-value-filter probes 2/8 → 7/8 (5 repaired);
  `ne` filters on grouped columns were never wrongly rejected.
* **Hints + repair wording + alias normalisation (v6).** 7B repaired every
  hinted mistake on answerable questions and over-refusals fell 1 → 0. 3B did
  not act on hints; its failures moved from wrong/invalid plans to declines
  (over-refusals 1 → 3, same correct count). Hints listing available measures
  did not induce substitute answers for undefined metrics.
* **Expression-kind hints (v7).** No measured accuracy change on fresh_v7
  (identical scores; one 7B over-refusal became an invalid plan). The hint
  fired twice for 7B and both repairs were still invalid. Motivated by a live
  app question ("total units sold by product category" → `kind: "sum"`),
  which v7 does repair; that question is excluded from all scored sets. The
  app uses v7 because it did not regress and its failure is an honest
  "could not build a valid query" rather than a false "measure does not
  exist".
* **Decline path in the column hint (v8) — rejected.** Intended to stop the
  column-as-measure hint enabling unsupported answers. On fresh_v8 it changed
  one case: 7B "sales target by store region" went from correct (the v7 hint
  led it to the existing `Sales Target` measure) to an over-refusal. The
  substitution probes (metrics that exist only as raw columns) were already
  declined under v7, and the unsupported questions still answered under both
  arms were planned directly without any hint. Net: −1 answerable, +0
  declines, so the app stays on v7.
* **Answerability gate (v9).** A deterministic trigger (question words not in
  the model vocabulary or a generic analytics list) runs a verifier only
  when needed. Calibrated on seen sets: fires on 0/113 answerable and 43/48
  unsupported questions. On unseen fresh_v9, 7B: answerable unchanged
  (12/12, 11 strict), unsupported questions answered anyway 2 → 0, declined
  5 → 7, no over-refusals; the verifier passed all 6 answerable questions
  containing benign unknown words. 3B: one gain (a what-if) and one false
  block ("best to worst" judged not a metric). The app (7B) adopted v9.
* **Grouped-column echo normalisation (v10).** A live app question ("gross
  profit by sales channel") failed because 7B also listed the grouping
  column as a `{"kind": "column"}` metric. v10 drops such echoes (the
  column is already in the result, so the computation is unchanged) and
  records the change. Evidence is an **offline replay, not a fresh A/B**:
  all 841 saved model outputs from fresh_v1-v9 re-parsed under v9 and v10
  gave 4 invalid → valid (all 7B; all fully covered their requirement
  specs) and 0 other changes. No accuracy gain is claimed beyond that.
* **No-metric gate trigger (v11).** v9's trigger only fires on
  out-of-vocabulary words, so vague requests written entirely in model
  vocabulary ("Show the breakdown") reached execution. v11 also runs the
  verifier when the question names no metric. On unseen fresh_v10 (terse
  answerable questions that name their metric; vague unsupported ones that
  do not), every answerable case had the same outcome under both arms and
  the new trigger fired on none of them. Unsupported declined: 7B 3 → 5
  ("What were the results for Q3?", "Show everything by month."), 3B
  5 → 6 ("Show the breakdown."). Three vague questions are still answered
  by both models under v11 ("Compare the store types.", "Show the figures
  by channel.", "Rank the regions."): the trigger's metric-word list
  contains countable entity words (store, channel, region), so it did not
  fire. The app (7B) adopted v11.
* **Terse questions (fresh_v10, both arms).** Independent of the gate, 7B
  answerable accuracy fell to 8/12 execution, 7 strict (12/12, 11 on
  fresh_v9): wrong metric for "How many orders per channel?" and "Gross
  profit per order by payment method.", wrong grouping/filter for "Gross
  sales for Riyadh stores by month." and "Units sold on weekends by
  channel.", and a same-table customer count for "Customers per segment
  who bought something." 3B over-refused 6 of 12. These are planner
  errors that no current check catches.
* **Narrower metric-word list (v12, rejected).** v12 removes grouping
  entities from the trigger's metric words so that "Rank the regions."
  reaches the verifier. On unseen fresh_v11 (prompt and parsing identical
  in both arms) it did
  what it was built for: 7B unsupported declined 0 → 6 of 8, 3B 5 → 7.
  But it also blocked two answerable questions on both models, "Distinct
  stores per channel." and "Distinct store cities in each region.":
  neither contains a quantity word or one of the counting phrases ("how
  many", "number of", "count"), so the trigger fired and the verifier
  rejected plans that were correct (7B passed both under v11; 3B passed
  one). 7B answerable fell 10 / 8 → 8 / 6 and over-refusals rose 1 → 3.
  The adoption rule fixed before the run was "more declines at no cost to
  answerable questions", so v12 is rejected and the app stays on v11.
  Two vague questions are still answered by 7B under v12: "Show the
  figures for each customer segment." ("customer" remains a quantity
  word; noted before the run) and "Sort the subcategories from highest
  to lowest." (trigger fired, verifier output was not valid JSON, and
  the gate passes on unusable verifier output).
* **fresh_v11, both arms.** "How many stores sold something in each
  region?" was over-refused by both models under both arms ("something"
  is out-of-vocabulary; the verifier rejected the plan). 7B produced an
  invalid plan for "Compare the regions on units sold per order." Under
  v11, 7B answered all 8 vague unsupported questions with a guessed
  metric.

## 4. Known limitations

* **Small samples, single runs.** 12–16 answerable cases per set, temperature
  0, one run per arm; one case ≈ 6–8 percentage points.
* **Author-written sets.** Questions, specs and references were written by
  the same engineer as the pipeline; each set probes the change being
  measured, so targeted gains are expected. Untargeted questions show smaller
  effects.
* **3B ceiling.** 3B frequently uses columns as measures and does not follow
  fix-it hints; its correct declines on unsupported requests also mostly come
  via the repair turn, so removing the decline option would cost declines.
* **Hints can enable unsupported answers.** On fresh_v7 (v6 and v7), the
  column-as-measure hint for `Total Orders` (a FactSales column) let 7B
  answer "total marketing spend by channel" with an unrelated query. The
  wording fix tried in v8 cost an answerable case and was rejected.
* **Directly answered unsupported questions** were the largest decline gap
  through v8 (the planner emits a valid plan, so no rule fires). The v9 gate
  addresses this for questions with unknown wording; questions phrased
  entirely in model vocabulary (e.g. "Show me the totals") still bypass it.
* **Verifier is a model judgement** (same local model, one extra call when
  triggered); 3B produced a false block on fresh_v9.
* **Vague requests that mention an entity** ("Rank the regions") still
  pass the v11 trigger and are answered with a guessed metric (3 of 8 on
  fresh_v10; 8 of 8 for 7B on fresh_v11). Narrowing the metric-word list
  (v12) catches 6 of those 8 but over-refuses "Distinct <entity> per
  <group>" questions, so it was not adopted. Treating "distinct" as a
  counting phrase is the obvious follow-up; it is untested and would need
  a new fresh set.
* **Gate fails open.** When the verifier's output cannot be parsed, the
  plan is executed (one unsupported question on fresh_v11, 7B v12).
* **Verifier false blocks on correct plans** occurred on fresh_v11 for
  every answerable question the trigger fired on (1 under v11, 3 under
  v12, both models), so widening the trigger has a direct cost.
* **Terse phrasing** lowered 7B answerable accuracy on fresh_v10 to 7/12
  strict; wrong-metric and wrong-grouping plans executed without warning.
* **Diminishing returns.** v7 and v8 produced no measured gain; further
  gains likely need real user questions rather than author-written sets.
* **Rule gaps (by design).** Rule 2 allows same-table dimension distinct
  counts (e.g. customers per segment from `DimCustomer`), which can include
  entities without sales (FRESH4-010 fails strict on both models).
* **Request guard** is a routing heuristic with a fixed vocabulary, not a
  security boundary; execution is read-only regardless.
* **Value catalog** is a snapshot; it must be re-captured when dimension
  values change (`python -m evaluation.capture_value_catalog`, refuses to
  overwrite).
* **Not yet covered:** row-level security, permission boundaries, data
  freshness, ties beyond deterministic tie-breakers, multi-turn context,
  `IN`-style multi-value filters (not expressible in the plan schema).
* **App integration is opt-in.** `INSIGHTPILOT_AGENT=structured` runs
  pipeline v11 in the Streamlit app (`src/structured_agent.py`, local
  `qwen2.5-coder:7b`); the default remains the original Foundry agent.
  Requirement coverage is not applied live (it needs a requirement spec).

## 5. Provenance

| Commit | Content |
|---|---|
| `c8435d7` | Structured plan pipeline, HOLD evaluation, requirement coverage |
| `6d55886` | fresh_v1 set (pre-generation) |
| `f0e06a4` → `13db552` | v2 pipeline → fresh_v2 set |
| `70dd29b` → `7de70ff` | v3 + multi-alias comparator → fresh_v3 set |
| `7ce6bf1` → `139d16a` | v4 pipeline → fresh_v4 set |
| `fd22675` → `7f80e22` | v5 (rule 3) → fresh_v5 set |
| `65843c1` → `74e27ff` | v6 → fresh_v6 set |
| `22adf9b` | Streamlit integration (opt-in) |
| `b01145b` → `ed45eeb` | v7 → fresh_v7 set |
| `0179ad4` → `fee3de4` | v8 → fresh_v8 set (v8 rejected) |
| `5db2a26` → `b89ea7f` | v9 → fresh_v9 set (app switched to v9) |
| `f831ee9` | v10 normalisation, offline replay, app switched to v10 |
| `0a9d34b` → `facba4f` | v11 → fresh_v10 set (app switched to v11) |
| `d467725` → `e563fd5` | v12 → fresh_v11 set (v12 rejected, app stays on v11) |

## 6. Reproducing

From the project root in the activated `.venv`:

```
python -m pytest tests/ -q
python -m evaluation.run_query_plan_evaluation                 # HOLD set
python -m evaluation.run_fresh_evaluation references --set fresh_v7
python -m evaluation.run_fresh_evaluation generate   --set fresh_v7 --prompt v7
python -m evaluation.run_fresh_evaluation evaluate   --set fresh_v7 --prompt v7
```

Run the app with the structured agent (stop running instances first):

```
.\stop_streamlit.ps1
$env:INSIGHTPILOT_AGENT = "structured"
python -m streamlit run src/main.py
```

Every stage refuses to overwrite an existing result file; delete or move a
result deliberately to re-run it. `references` and `evaluate` need the
Power BI delegated sign-in (`ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`);
`generate` needs a local Ollama with both Qwen models.
