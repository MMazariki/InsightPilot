# InsightPilot status snapshot, 2026-11-17

Git tag: `snapshot-2026-11-17` (taken before the chart-for-every-question change that followed the same day). All 678 offline tests pass
(`.venv\Scripts\python.exe -m pytest tests -q`). The app is running.

## What exists and works

| Area | State | Verified how |
|---|---|---|
| Always-on app | Scheduled task `InsightPilot` starts it at sign-in on `http://localhost:8501`; Desktop and Start Menu shortcuts open, restart and stop it | Start, stop and crash-recovery run on this machine. A real sign-out/sign-in was not tested. |
| Interface | InsightPilot branding, starter questions, New chat, collapsible query details | Seen in the live page |
| Local assistant | Pipeline v11 on `qwen2.5-coder:7b` (Ollama), read-only Power BI queries | Real questions answered in the live app |
| Admin page | Gear button or `?view=admin`: models and providers, answer checks, Power BI, interface, voice | Live page; local model test button. Cloud providers (OpenAI-compatible, Anthropic) are unit-tested with fakes only and have never been called live. |
| Charts in the report | Every answered question builds a visual from the checked plan (card, pie, donut, column, bar, line, combo, legend, table; a chart type named in the question wins) with the answer's filters and top-N applied; it replaces whatever is on the AI Canvas page. Report component rebuilt 2026-11-17. | The request reaches the panel (log) and the rebuilt component loads. Drawing, filters, titles and replacement inside the signed-in report were NOT seen by the developer; filter or title failures are caught and reported in the panel's status line. |
| Report page link | The "AI Canvas" page is looked up by name, so republishing cannot break it | Live lookup returned the current page |
| Voice input | Microphone in the question box, local whisper.cpp (`base.en`), review-then-send | Fake microphone in a real browser; the user's own recordings were transcribed on 2026-11-17 (log: 6.5 s and 7.7 s clips, 41 and 52 characters) |
| Data | Source CSVs extended with generated 2026 data (27,143 lines, 1 Jan to 16 Nov 2026); dataset refreshed and republished by the user | Published dataset matches the CSVs month by month |

## Open items

1. **Targets ignore dates (Power BI model).** `FactTarget` is related to
   stores but not to dates, so target, variance and target-achievement
   figures are two-year totals whatever year or month is asked. The user
   chose to leave this for now. The fix is a month table in the model; the
   app's `evaluation/semantic_model_schema.json` then needs the new
   relationship.
2. **Accuracy is unmeasured on the current data.** Everything in
   `docs/EVALUATION.md` was measured on the 2025-only dataset. Questions that
   span two years (for example "net sales by month" without a year) have
   never been measured and may merge the same month of both years.
3. **Pipeline v12 was rejected**; a candidate v13 (treat "distinct" as a
   counting phrase, decline when the verifier's reply is unusable) is
   untested and needs a new pre-registered question set. The second half is
   already available as an Admin switch, off by default.
4. **All evaluation questions were written by the developer.** The user has
   not yet run their own set of questions through the app.
5. **Cloud models** need an API key and a first live test from the Admin
   page before they can be relied on.
6. **Memory.** Windows had no page file; the user enabled it on 2026-11-17
   after Ollama failed to load the model. If "out of free memory" returns,
   close large applications.
7. **Measure names.** A "net"/"gross" phrase that names no measure (for
   example "net profit"; the model has Gross Profit and Net Sales) is now
   declined with the closest measure instead of being answered with a
   different one. Before this, "Net profit over years" returned Net Sales
   labelled "Net Profit". The check was added after v11 was measured and is
   an Admin switch (Answer checks), on by default.
8. **Everyday wording** (`src/interpret.py`, added 2026-11-17): synonyms,
   relative dates, typos, "compare A and B" as a breakdown, date filters
   compiled as DAX dates, and suggested rephrasings on a decline. On a
   probe of 24 loosely worded questions written by the developer, 21 were
   answered and 3 declined with suggestions, against 4 declined and at
   least 4 silently wrong before ("this year" read as 2023, "last month"
   as April 2023, "compare 2025 and 2026" returning nothing). This is a
   spot check, not a measured evaluation.
9. **Voice.** English only. If words are misheard, switch the speech model to
   `small.en` in Admin settings and restart.

## Where things are

* How to use and set up: `README.md`
* Measured results and limitations: `docs/EVALUATION.md`
* Voice implementation record: `docs/VOICE_INPUT_PLAN.md`
* Source data: `L:\Downloads\Saudi_Retail_MVP_PowerBI_Dataset` (original
  2025-only files in the sibling folder ending `_backup_2025_only`)
* Power BI file: `L:\Others\AgenticProject.pbix`
* Settings and encrypted API keys: `config\` (not committed)
* Logs: `logs\`
