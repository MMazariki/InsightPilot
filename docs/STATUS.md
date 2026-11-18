# InsightPilot status snapshot, 2026-11-17 (night)

Git tag: `snapshot-2026-11-17e`. All 803 offline tests pass
(`.venv\Scripts\python.exe -m pytest tests -q`). The app is running.
Earlier tags the same day: `snapshot-2026-11-17` (morning),
`snapshot-2026-11-17b` and `-c` (evening, before and after the weak-spot
fixes in item 3), `-d` (Documents page with folder segregation, before
its settings moved to Admin).

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
| Documents page (RAG) | `?view=documents` or the Documents button. Answers only from the PDFs of the folder number entered (`documents\<number>`, or `?folder=` in the link); nothing without one. pypdf, `nomic-embed-text` + keyword search fused, `qwen2.5:7b-instruct`; every answer cites file and page; incremental indexing; per-folder library and history; Read aloud (browser speech, local voices); every parameter (models, answer instructions, search, follow-ups, indexing, page) set in Admin → Documents, with an automatic rebuild when an index-shaping setting changes | Unit tests incl. cross-folder isolation; live on the user's PDF in folder 102 and on the project guide as a PDF. Accuracy not measured. Read aloud not heard by the developer (needs a real click). |
| Voice input | Microphone in the question box, local whisper.cpp (`base.en`), review-then-send | User's own recordings transcribed |
| Data | 1 Jan 2021 to 16 Nov 2026, 122,483 sales lines: 2021-2024 and 2026 generated, 2025 as delivered. Targets rescaled to actual sales + 5-15% | Published dataset matches the CSVs year by year (lines, net sales, orders, targets) |
| Agent actions (`src/actions/`, `src/action_card.py`) | "Email this to Sara", "post this to Teams", "create a task for every store below 85%", "save this as Excel", also combined with a question. Drafted on an editable card; runs only on Approve, once; 15-minute expiry; address book and allowed domains only; content built by the app; daily caps; audit log; Admin → Actions (off by default) | 59 unit and page tests (fakes for Graph and Teams); pre-registered rules check 32 / 32 (`docs/EVALUATION.md` §3.6). **Tested by hand in the browser, 2026-11-18:** an email (SMTP, to the user's own address; user confirmed it arrived), a calendar reminder (`.ics`, Friday read correctly, alert 09:00) and an Excel export, each drafted, approved and logged. The export showed percentages as text; fixed so they are numbers. No Teams post or To Do task (not available on the account, item 11). |
| Sign-in and access (`src/security/`, `src/access_page.py`) | Added 2026-11-19. First-run administrator account. Every page needs a sign-in. Six permissions per user (Power BI, Documents, Actions, Evaluation, Admin page, Users & folders). Documents folders assigned per user (grid), with only assigned folders listable or searchable. scrypt password hashes; lockout; idle sign-out; forced change of temporary passwords; security log `logs\security.jsonl`; recovery script `scripts\reset_user_password.py` | Unit and page tests (AppTest), 900 tests in all. **Used in the browser 2026-11-19:** administrator account created, test users added, password reset and forced change, folder access changed, a wrong password refused (security log). The quick action buttons under an answer were used to send an email (action log). |
| Model | Measures synced by `scripts/sync_model_schema.py`: Cost, Quantity, Orders added; `TotalQuantity` column. Sales Target follows date and category (TREATAS); Variance = target - net sales; achievement about 91% (2026: 79%, November and December are full-month targets) | Live DAX checks against the CSVs |

## Open items

1. **Accuracy measured on the current setup (2026-11-17, one run each,
   developer-written sets; `docs/EVALUATION.md` §3.4-3.5).** Power BI
   assistant: 29 / 33 (answerable 23 / 27, declines 6 / 6). Documents page:
   right page retrieved 20 / 20, correct answer with correct citation
   13 / 20, unanswerable declined 5 / 5. Known defects found, not yet fixed
   (a fix needs a new set to measure it): "compare 2025 and 2026" shows all
   years; "given" sends a clear question to the verifier; the planner can
   add an unrequested month grouping or pick a raw column; the documents
   answer model sometimes cites in an unparsed form or the wrong passage, or
   says "not found" with the passage in hand.
2. **Unit Price** still adds up prices (2,153,646 against an average of
   17.58 SAR) and is kept out of the app. Fix in Power BI:
   `Unit Price = AVERAGE(FactSales[UnitPriceSAR])`, then run
   `scripts\sync_model_schema.py --write`.
3. **Weak questions fixed (2026-11-17):** ratios
   ("units per order", "average order value") divide by orders (AOV was
   computed as net sales / units); `FactSales[Total Orders]` is kept out of
   the app by `scripts/sync_model_schema.py`; "by month" without a year
   shows each month of each year (ask for the month of the year to combine
   years); month, day and time columns are listed in time order. Note: in
   the 2025 data as delivered some orders span several stores, so
   per-region order counts add up to more than the distinct total.
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

10. **Documents page:** folders are now assigned per signed-in user
    (2026-11-19). The files themselves are plain files on disk, readable by
    anyone with the Windows account. Answer quality
    depends on the 7B model (it once listed a diploma as a certification).
    No measured question set exists yet for documents.
11. **Agent actions on this machine (2026-11-18).** Graph consent for
    `Mail.Send` and `Tasks.ReadWrite` is granted, but the Power BI account
    (`…onmicrosoft.com`) has **no Exchange mailbox**
    (`MailboxNotEnabledForRESTAPI`), so Graph email and To Do cannot work,
    and Teams is not available on the account type (switched off). Email is
    therefore set to go through the user's own account by SMTP (Gmail app
    password, to be entered in Admin → Actions), and tasks are saved as
    calendar reminders (`.ics`). **First real email sent 2026-11-18**: SMTP
    account set up, actions enabled, "email this to me" drafted, approved
    and sent from the chat card (audit log: drafted, approved, sent); the
    user confirmed it works. A `.ics` reminder and an Excel export were also
    run from the chat. Still to do: address book and allowed domains (empty,
    so only "me" resolves).
    Detection is rules only: unknown wordings ("loop in Sara") are treated
    as questions.

## Where things are

* How to use and set up: `README.md`
* Measured results and limitations: `docs/EVALUATION.md`
* Source data: `L:\Downloads\Saudi_Retail_MVP_PowerBI_Dataset`; backups in
  the sibling folders ending `_backup_2025_only`, `_backup_before_history`
  and `_backup_before_targets`
* Data scripts: `scripts/extend_sales_data.py` (2026),
  `scripts/add_history_years.py` (2021-2024), `scripts/rescale_targets.py`,
  `scripts/sync_model_schema.py` (model measures into the app)
* Documents: PDFs in `documents\<folder number>\`, index in `rag_index\`
  (both gitignored); design in `docs/RAG_PLAN.md`
* LinkedIn post and screenshots: `docs/linkedin/`
* Power BI file: `L:\Others\AgenticProject.pbix`
* Settings and encrypted API keys (and the Teams webhook): `config\` (not committed)
* Actions: design `docs/AGENT_ACTIONS_PLAN.md`; exports in `exports\`, audit
  log `logs\actions.jsonl` (both gitignored)
* Logs: `logs\`
