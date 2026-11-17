# InsightPilot status snapshot, 2026-11-17 (evening)

Git tag: `snapshot-2026-11-17b`. All 758 offline tests pass
(`.venv\Scripts\python.exe -m pytest tests -q`). The app is running.
The earlier tag `snapshot-2026-11-17` marks the morning state.

## What exists and works

| Area | State | Verified how |
|---|---|---|
| Always-on app | Scheduled task `InsightPilot` starts it at sign-in on `http://localhost:8501`; Desktop and Start Menu shortcuts open, restart and stop it | Start, stop and crash recovery on this machine. A real sign-out/sign-in was not tested. |
| Interface | Branding, starter questions, New chat, collapsible query details, suggested rephrasings as buttons | Live page |
| Local assistant | Pipeline v11 on `qwen2.5-coder:7b` (Ollama), deterministic DAX, read-only Power BI queries | Live questions |
| Everyday wording (`src/interpret.py`) | Synonyms (revenue, branch, pieces, period, performance ...), relative dates (this/last year, month, quarter, today, yesterday), typos, "orders" as distinct orders, "missed/beat its target", "A vs B" for two measures; plan fixes (compare two years as a breakdown, year-month and quarter text filters, date text as DAX dates); the answer says how it read the question | Developer probes on live data (about 50 questions) |
| Growth, share, periods (`src/analysis.py`) | "grew the most between 2021 and 2026", year over year, "Delivery share of net sales by year", Ramadan / Eid / National Day / White Friday by year | Developer probes on live data |
| Charts in the report | Every answered question draws a visual from the checked plan (card, pie, donut, column, bar, line, combo, legend, 100% stacked share, table), with the answer's filters and top-N, at (0,0) at the full AI Canvas page size, replacing the previous one. Chart type selector (Auto, Pie, Donut, Column, Bar, Line) redraws without a new query | **User confirmed** the chart draws and fills the canvas in the signed-in report (2026-11-17). The selector was checked in the app; its redraw was not watched in the signed-in report. |
| Report panel | Account chooser always shown, the query account suggested, signed-in account shown with a warning if different, "Use a different account"; side panes collapsed; AI Canvas page found by name | Panel loads in a browser; sign-in flow not watched by the developer |
| Admin page | Models and providers, answer checks (incl. measure-name check and everyday wording, both on), Power BI, interface, voice | Live page. Cloud providers are unit-tested with fakes only. |
| Voice input | Microphone in the question box, local whisper.cpp (`base.en`), review-then-send | User's own recordings transcribed |
| Data | 1 Jan 2021 to 16 Nov 2026, 122,483 sales lines: 2021-2024 and 2026 generated, 2025 as delivered. Targets rescaled to actual sales + 5-15% | Published dataset matches the CSVs year by year (lines, net sales, orders, targets) |
| Model | Measures synced by `scripts/sync_model_schema.py`: Cost, Quantity, Orders added; `TotalQuantity` column. Sales Target follows date and category (TREATAS); Variance = target - net sales; achievement about 91% (2026: 79%, November and December are full-month targets) | Live DAX checks against the CSVs |

## Open items

1. **Accuracy is unmeasured on the current setup.** `docs/EVALUATION.md`
   measured pipeline v11 on the 2025-only data and the old model. Since
   then the data covers six years, the model has new measures, and three
   layers were added after v11 (measure-name check, everyday wording,
   growth/share/periods). They were checked with developer-written probes,
   not a pre-registered question set. A fresh evaluation set over the
   current data would measure them.
2. **Unit Price** still adds up prices (2,153,646 against an average of
   17.58 SAR) and is kept out of the app. Fix in Power BI:
   `Unit Price = AVERAGE(FactSales[UnitPriceSAR])`, then run
   `scripts\sync_model_schema.py --write`.
3. **Known weak questions:** ratios such as "units per order by channel"
   fail validation; the `FactSales[Total Orders]` column (the same grand
   total on every row) can mislead the planner; "net sales by month"
   without a year adds the same month of all six years together.
4. **Rules are fixed lists.** Wording the rules do not know falls back to
   the model and the answer checks. New phrases are added in
   `src/interpret.py` and `src/analysis.py`.
5. **Pipeline v12 was rejected**; candidate v13 is untested.
6. **The user has not yet run their own question set** through the app.
7. **Cloud models** need an API key and a first live test from the Admin
   page.
8. **Memory.** If "out of free memory" returns, close large applications
   (the Windows page file was enabled on 2026-11-17).
9. **Voice** is English only; `small.en` is the next step if words are
   misheard.

## Where things are

* How to use and set up: `README.md`
* Measured results and limitations: `docs/EVALUATION.md`
* Source data: `L:\Downloads\Saudi_Retail_MVP_PowerBI_Dataset`; backups in
  the sibling folders ending `_backup_2025_only`, `_backup_before_history`
  and `_backup_before_targets`
* Data scripts: `scripts/extend_sales_data.py` (2026),
  `scripts/add_history_years.py` (2021-2024), `scripts/rescale_targets.py`,
  `scripts/sync_model_schema.py` (model measures into the app)
* Power BI file: `L:\Others\AgenticProject.pbix`
* Settings and encrypted API keys: `config\` (not committed)
* Logs: `logs\`
