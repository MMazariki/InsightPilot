# InsightPilot — Structured Planning Evaluation

Last updated: 2026-11-19 (§3.7 whole app, new set). Earlier: 2026-11-17 (§3.4 whole app, §3.5 Documents page). All numbers below were measured by executing
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
  in both arms, and all 40 pre-gate plans were identical) it did
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

### 3.4 Whole-app evaluation, 2026-11-17 (app_cases_v1)

Sections 3.1-3.3 measure the planner pipeline on the 2025-only data. This
set measures the **app as shipped on 2026-11-17**: every question goes
through `StructuredPlanningAgent.ask` with the app's settings (pipeline v11
plus the measure-name check, everyday-wording interpretation, plan fixes and
the growth/share/period analysis layer) against the six-year dataset, and
the final answer table is compared **by values** with hand-written
reference DAX (`evaluation/run_app_evaluation.py`; column names ignored,
percentages shown with one decimal accepted within 0.06 points; ranking
questions compare the first row). "Today" was fixed to 2026-11-17. Set
committed before the run (`982f328`), harness before the run (`c7100c4`).
Model `qwen2.5-coder:7b`, one run.

| Group | Passed |
|---|---:|
| Everyday wording (branch, turnover, typos, this year, last month, orders, AOV, by month) | 12 / 15 |
| Growth, year over year, share, Ramadan, White Friday | 8 / 8 |
| Targets (achievement, target vs sales, missed target, variance) | 3 / 4 |
| Should decline (forecast, write, net profit, missing data, vague) | 6 / 6 |
| **All** | **29 / 33** (answerable 23 / 27) |

Failures:

* APP1-008 "Units sold by category in 2025": the planner added an
  unrequested YearMonth grouping (48 rows instead of 4).
* APP1-013 "Discounts given per channel in 2024": "given" is not a known
  word, so the verifier ran and wrongly declined (over-refusal).
* APP1-014 "Compare net sales in 2025 and 2026": the app's
  repeated-filter rule turns the two year filters into a breakdown but
  drops the restriction to those years, so all six years are shown. A
  defect in `src/interpret.py`, not in the model.
* APP1-025 "Sales target vs net sales by region in 2025": the planner used
  the column SalesTargetSAR, the repair turn did not recover, declined.

Caveats: 33 questions, written by the developer who also wrote the
interpretation rules, so wording known to the rules is over-represented;
one run; no confidence interval. The failures above were not fixed against
this set; a fix needs a new set to be measured.

### 3.5 Documents page, 2026-11-17 (doc_cases_v1)

`evaluation/run_doc_evaluation.py` on four project documents turned into
PDFs from revision `c7100c4` (28 pages, folder `eval`; the user's own PDFs
are private and not used). App settings: `nomic-embed-text` + keyword
search, `qwen2.5:7b-instruct`, 6 passages. Set committed before the run
(`8a3d324`).

| Measure | Result |
|---|---:|
| An acceptable page among the passages given to the model (retrieval) | 20 / 20 |
| Answer contains the required words | 16 / 20 |
| Answer cites an acceptable page | 14 / 20 |
| Correct answer **and** correct citation | **13 / 20** |
| Unanswerable questions answered "The documents do not answer this." | 5 / 5 |

Search found the right page every time; the misses are in the answer
model: citations in an unparsed form (the whole answer in brackets, a
Markdown link "(1)") in 2 cases, the wrong source number cited in 2,
"do not answer" although the passage was given in 2 (small.en accuracy,
default minimum relevance), half an answer in 1 (temperature but not the
quantisation), and 1 answer that is right in substance but lacks the
pre-registered words ("a routing heuristic, not a security boundary";
counted as a failure). Caveats: the developer wrote both the documents and
the questions, so questions are worded close to the text; one run.

### 3.6 Agent actions, 2026-11-17 (action_cases_v1)

`evaluation/run_action_evaluation.py` on 32 chat messages (set committed
before the run, `1c79734`): 13 action requests of the four kinds, 4 mixed
question + action requests, 4 recipient problems (unknown name, outside
domain, ambiguous role), 7 questions that only look like actions, and 4
injection cases where a table value contains an instruction. No model and
no network: detection and recipients are rules, executors are a counter.

| Measure | Result |
|---|---:|
| Right action kind, or none for a question | 32 / 32 |
| Data question extracted from a mixed request | 4 / 4 |
| Right recipients; unknown names left for the user to choose | 13 / 13 |
| No action executed without an approval on a shown card | 23 / 23 |
| Injected table text added no draft, no recipient, no markup | 4 / 4 |

**Read this as a regression check, not as accuracy.** The same person wrote
the rules and the cases, after the rules, and everything is deterministic,
so a full score is expected; it shows the listed behaviours hold. Wordings
that the rules do not know (for example "loop in Sara", "ping the finance
team") are not recognised and are answered as questions. Real sends to
Outlook, Teams and To Do have not been made yet (they need the one-time
setup in `docs/AGENT_ACTIONS_PLAN.md` §6).

### 3.7 Whole-app evaluation, 2026-11-19 (app_cases_v2)

The same method as §3.4 on a **new set of 31 questions**, none repeated from
v1. The set was committed (`de9454f`) before the fixes for the four v1
failures (`cd03084`) and before any run. The four v1 defect types get two
new questions each; the rest is new wording (unit price, average selling
price, quarterly, top 3, weekends), analysis periods and declines. "Today"
fixed to 2026-11-19, model `qwen2.5-coder:7b`, app settings as shipped, one
run.

| Group | Passed |
|---|---:|
| The four v1 defect types (compare two years, unasked month grouping, "given/granted/offered", target column) | 7 / 8 |
| Wording | 11 / 13 |
| Analysis (growth, share, Eid, National Day) | 3 / 4 |
| Should decline | 5 / 6 |
| **All** | **26 / 31** (answerable 21 / 25) |

Failures, as the run showed them (not fixed against this set; a fix needs a
new set to be measured):

* APP2-002 "Compare units sold in 2021 and 2024": the plan was rejected
  ("Invalid sorting field: DimDate[Year]"): the planner sorted by Year
  without grouping by it, and the app's two-year fix runs only after a plan
  passes that check.
* APP2-015 "Net sales on weekends vs weekdays": 3 rows instead of 2.
* APP2-020 "How many customers bought something in 2025?": declined
  (over-refusal).
* APP2-023 "In-Store share of orders by year": no row matched the
  reference (the share or the order count was computed differently).
* APP2-027 "Update the sales target for Riyadh Mall 1 to 50000": answered
  with a read-only query instead of being declined. Nothing can be written
  (queries are read-only), but the request should have been refused; the
  request guard does not know "update ... to".

The harness crashed while writing the result file, after every question had
run (a relative `--set` path; fixed afterwards). The scores above are from
that run's printed log, kept as
`evaluation/results/app_cases_v2_20261119-run1-log.txt` (gitignored). It
was not re-run.

Caveats: 31 questions, written by the developer who also wrote the rules
and the fixes, so the set still leans towards wording the rules know; one
run; no confidence interval. The v1 defect questions (7 / 8) show that the
fixes generalise to new wording, not that the app is 84% accurate in
everyday use.

### 3.8 Whole-app evaluation, 2026-11-19 (app_cases_v3)

A third new set of 20 questions, committed (`1f046ea`) before the fixes for
the five v2 failures (`dd61e9f`) and before any run. Each v2 failure type
gets two new questions (write requests three), plus new wording, a growth
question and declines. Same method and model as §3.7, one run; result file
`evaluation/results/app_cases_v3_20261119-032732.json` (gitignored).

| Group | Passed (as run) |
|---|---:|
| Two non-adjacent years | 2 / 2 |
| Weekends vs weekdays | 1 / 2 |
| Counting customers | 2 / 2 |
| Share of orders | 0 / 2 |
| Write requests refused | 3 / 3 |
| Wording, growth | 7 / 7 |
| Other declines | 1 / 2 |
| **All** | **16 / 20** |

**Two of the four failures are errors in the reference queries, not in the
app.** APP3-007 and APP3-008 ("Card share of orders by year", "Click &
Collect share of orders by region") divide by `DISTINCTCOUNT(OrderID)`
written inside `ADDCOLUMNS` without `CALCULATE`, so the denominator is every
order in all six years (62,067) instead of the orders of that year or
region. The app's shares are the correct ones (2021: 3,554 Card orders of
6,750 = 52.7%; the reference said 5.7%). Comparing the saved answers, with
the harness's own comparison and without re-running the app, against the
references with `CALCULATE` added: both match. **With those two references
corrected: 18 / 20.** The committed set is left unchanged. The same
reference error is in v2's APP2-023 (§3.7). That v2 answer was also wrong:
its share was the sum of the parts, fixed in `dd61e9f`.

Real failures:

* APP3-004 "Weekend versus weekday gross profit in 2025": read as "By
  IsWeekend gross profit in 2025" and declined by the answer check. The
  rewrite works when the weekend words come after the measure (APP3-003
  passed), not before it.
* APP3-019 "What is our market share in Saudi Arabia?": answered (as a
  share of net sales) instead of declined; market share needs data from
  outside the company.

Caveats as in §3.7: written by the developer who also wrote the rules and
the fixes (the write-request questions test verbs added in the same fix),
20 questions, one run.

### 3.9 Whole-app evaluation, 2026-11-19 (app_cases_v4)

A fourth new set of 11 questions, committed (`e1a2727`) before the fixes
for the two v3 failures (`d385471`) and before any run: new questions for
those two failure types, plus questions that must keep working next to the
fixes. The share references were checked before committing (store-type
shares add up to 100%). Same method and model, one run; result file
`evaluation/results/app_cases_v4_20261119-034641.json` (gitignored).

| Group | Passed |
|---|---:|
| Weekend words before the measure | 2 / 2 |
| Outside data declined (competitors, market size, industry average) | 3 / 3 |
| Shares of the company's own totals (must keep working) | 2 / 2 |
| Weekend and day of week (must keep working) | 1 / 2 |
| Wording | 1 / 2 |
| **All** | **9 / 11** |

Failures (not fixed against this set):

* APP4-008 "Net sales on weekends in 2026": the planner added a day-name
  breakdown (3 rows instead of one total). The unasked-grouping fix keeps
  day groupings when the question mentions weekends, because it cannot
  tell "on weekends" (a filter) from "by weekend" (a breakdown).
* APP4-010 "Gross sales by channel last month": the planner grouped by the
  year it also filtered to one value; the check rejected it and the repair
  turn did not recover.

## 4. Known limitations

* **The dataset changed after these measurements.** Every result in this
  document was measured on the original source data (1 Jan - 31 Dec 2025,
  24,000 sales lines). On 2026-11-16 the source CSV files were extended
  with generated 2026 data (27,143 lines for 1 Jan - 16 Nov 2026, plus
  2026 dates and targets) by `scripts/extend_sales_data.py`; no 2025 row
  was changed. Once the Power BI dataset is refreshed from those files,
  questions without a year filter return different numbers, so the saved
  reference results can no longer be re-captured, and a re-run of any
  evaluation would need new references. Plan validity is unaffected
  (no table, column, measure or catalogued value changed). The planner
  has never been measured on questions that span two years.

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
* **Verifier blocks answerable questions.** On fresh_v11 it blocked
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
