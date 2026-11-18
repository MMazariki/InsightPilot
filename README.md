# InsightPilot

Ask questions about your Power BI sales model in plain language. InsightPilot
plans a query with a local model, validates it, and reads the answer from
Power BI with a read-only query. The live report sits beside the chat.

## Using it

After the one-time setup below, InsightPilot is always running in the
background while you are signed in to Windows.

| Button | Where | What it does |
|---|---|---|
| **InsightPilot** | Desktop, Start Menu | Opens the app in your browser (starts it first if needed) |
| **Restart InsightPilot** | Start Menu → InsightPilot | Restarts the background server and opens the app |
| **Stop InsightPilot** | Start Menu → InsightPilot | Stops the server until the next sign-in or the next time you open it |

The app address is <http://localhost:8501>. It is reachable from this
computer only.

## Sign-in, users and folder access

Everyone signs in (`src/security/`).

* **First run:** when no account exists, the app asks to **create the
  administrator account**. That account has every permission and every
  document folder.
* **Users and folder access** (the people icon next to the gear, for users
  with that permission) has four tabs:
  * **Users:** add a user (a temporary password is shown once; they choose
    their own at the first sign-in), and edit, deactivate or unlock a user.
    Under **Edit a user**:
    * **Change password** types a new password for that user, with an
      option to make them change it at the next sign-in.
    * **Temporary password** has the app make one and show it once.
    * **Delete user** removes the account; it asks you to confirm.
    * You change your own password from the account menu instead.
  * **Document folders:** a grid of folders × users. Tick who may open each
    folder; you can also create a folder there.
  * **Sign-in policy** and the **Security log**.
* **Permissions** are assigned per user. Each one opens a part of the app;
  the buttons and pages a user may not use are hidden, and opening them by
  link sends the user back.

  | Permission | Opens |
  |---|---|
  | Power BI | the main chat and report |
  | Documents | the Documents page, only the folders assigned to the user |
  | Actions | email, reminders and exports from the chat |
  | Evaluation | the evaluation dashboard |
  | Admin page | Admin settings (models, checks, Power BI, actions), Alerts, and the Documents index buttons |
  | Users & folders | the Users and folder access page |

* **Passwords** are stored as salted scrypt hashes in `config\users.json`
  (not committed); never in clear and never in a log.
* **Lockout:** after 5 failed sign-ins the account is locked for 15 minutes.
* **Idle sign-out** after 8 hours.
* **Reloading the tab keeps you signed in.** The browser keeps a random
  token in a cookie for this site; only its hash is stored. It ends after
  8 hours unused, at sign-out, and when the password changes or the account
  is deactivated or deleted. A password change ends it in every browser.
* All of these limits can be changed under Sign-in policy.
* The app always keeps at least one active account that can manage users;
  you cannot delete, deactivate or demote yourself.
* **Forgotten administrator password:** run
  `.venv\Scripts\python.exe scripts\reset_user_password.py <username>`
  on this PC. It sets a temporary password that must be changed at sign-in.
* **Limits:** this protects the app on this PC (it listens on `localhost`
  only). The PDFs, settings and logs are ordinary files, so anyone with
  access to the Windows account can still read them. Power BI queries run
  as the one configured Power BI account for every user. Email actions go
  from the one configured email account.

## Asking in your own words

Before a question is planned, everyday wording is read into the model's
terms, and the answer starts with a line saying how it was read:

| You say | Read as |
|---|---|
| revenue, turnover, income, earnings, money, sales | net sales |
| branch, shop, outlet, location | store |
| pieces, quantity, volume | units sold |
| profitability, profit margin | gross margin % |
| performance, "how did we do", "perform best" | net sales (highest / lowest) |
| period, over time | year and month |
| day of week, weekday | day name |
| this / last year, month, quarter; today; yesterday | the actual dates, from today's date |
| typos ("regoin", "reveneu") | the closest model word |

"Compare 2025 and 2026" is answered as a breakdown by year. Three kinds of
question are worked out around the query (`src/analysis.py`):

* **growth**: "Which store grew the most between 2021 and 2026?", "How did
  units sold change between 2023 and 2026 by category?", "Net sales growth
  year over year" give each value, the change and the change %, ranked by
  change %;
* **share**: "Delivery share of net sales by year", "Share of net sales by
  channel", "Cash share of orders by region" give each part's % of its
  total (a 100% stacked or pie chart in the report);
* **holiday periods**: Ramadan, Eid (al-Fitr), National Day and White
  Friday, with a year or else the latest one, become exact date ranges.

Month and day names are listed in calendar order. When a question
still cannot be answered (it names no measure, or a measure the model does
not have, such as net profit), the answer suggests rephrased questions as
buttons. All of this is switched off with Admin → Answer checks →
"Understand everyday wording". It was added after the accuracy in
`docs/EVALUATION.md` was measured and is not covered by it.

## Asking your documents (PDFs)

The **Documents** button next to "New chat" (or
<http://localhost:8501/?view=documents>) opens a second chat that answers
only from the PDF files in the project's `documents` folder (another folder
can be set in Admin → Documents). Every answer names the file and page of
its sources, shows the passages used, and offers the PDF to open. When the
documents do not cover a question, it says so instead of guessing.

* **Folders are separate.** Put each group of PDFs in its own numbered
  subfolder (for example `documents\102`). A question is answered only from
  the folder chosen in **Folder**, and the list holds only the folders an
  administrator assigned to the signed-in user (see "Sign-in, users and
  folder access" above). A link such as
  <http://localhost:8501/?view=documents&folder=102> opens that folder only
  if it is assigned to the user. The library, sources and chat history show
  only that folder. PDFs placed directly in `documents` are never searched.
* **Adding files:** copy PDFs into a folder under `documents`. New,
  changed and deleted files are indexed when the page opens, or with
  **Index now**. Only text PDFs are read; scanned PDFs are listed as "no
  text layer".
* **Local:** text extraction with `pypdf`, search with the Ollama model
  `nomic-embed-text` (meaning) plus keyword matching, answers by
  `qwen2.5:7b-instruct`. Nothing leaves this computer. The PDFs and the
  index (`rag_index/`) are not committed to git.
* **Asking by voice** works as on the Power BI page: click the microphone
  in the question box, speak, click the tick; the words appear in the box
  to check (or are sent at once when Admin → Voice → auto-send is on).
  Speech is turned into text on this computer (whisper.cpp), with a
  neutral prompt rather than the Power BI page's sales vocabulary.
* **Read aloud** under each answer speaks it with the browser's own voices
  (local Windows voices such as Microsoft David or Zira are preferred, so
  the text is not sent anywhere); click again to stop. Switch it off in
  Admin → Documents.
* **Search only in** limits a question to chosen files; short follow-up
  questions ("and for returns?") use the previous question too.
* Design and decisions: [`docs/RAG_PLAN.md`](docs/RAG_PLAN.md).

## Charts in the Power BI report

When you are signed in to the report on the right, every answered question
also puts a visual on the report's "AI Canvas" page, replacing the previous
one. It is built from the checked query plan, not by a model:

* a single total becomes a card; one breakdown becomes a pie (a
  part-of-whole answer with up to six slices, or a question about share,
  split or breakdown), otherwise a column, bar or line chart
  (column-and-line for two measures); two breakdowns use the second as the
  legend; three or more become a table;
* the **Chart type** selector above the report (Auto, Pie, Donut, Column,
  Bar, Line) redraws the latest answer's chart without asking again; pie
  and donut appear when the answer has one breakdown and one measure;
* you can name the chart: add "as a pie chart", "donut chart", "bar chart",
  "column chart" or "line chart" to the question; "create a chart of ..."
  without a type also works (the words are removed before the question is
  checked);
* the answer's filters, including "top N", are applied to the visual so it
  shows the same rows as the table, and the question is its title;
* a calculated ratio (for example units per order) cannot be shown in a
  report visual; the answer says when something was left out.

After changing `frontend/src/index.ts`, rebuild with `npm run build` in
`powerbi_embed_component/powerbi_embed_component/frontend` and copy
`build/index-*.js` over the one in
`.venv/Lib/site-packages/powerbi_embed_component/frontend/build`.

## The data

The Power BI dataset is imported from CSV files in
`L:\Downloads\Saudi_Retail_MVP_PowerBI_Dataset` (synthetic Saudi retail
data, 1 Jan 2021 to 16 Nov 2026, 122,483 sales lines). 2025 is the data
as delivered. `scripts/extend_sales_data.py` generated 2026 (on
2026-11-16) and `scripts/add_history_years.py` generated 2021–2024 (on
2026-11-17), with yearly growth, Ramadan and Eid on each year's dates,
National Day and White Friday peaks, and delivery and wallet payments
gaining share. Backups: the sibling folders ending `_backup_2025_only` and
`_backup_before_history`. Changes to the CSV files reach the app only after
the dataset is refreshed: open the report's `.pbix` in Power BI Desktop,
choose Refresh, then Publish to the same workspace.

**New measures.** After adding or renaming measures or columns in Power BI
Desktop and publishing, run
`.venv\Scripts\python.exe scripts\sync_model_schema.py` to see the
differences and add `--write` to make them available to questions (then
restart the app). Check what a new measure computes first; `--skip "Name"`
keeps one out.

## Acting on an answer (email, Teams, To Do, export)

After a data answer, ask in the chat:

* "Email this to Sara", "send it to me", "share the table with the CFO"
* "Post this to the Teams channel"
* "Create a task for every store below 85%", "remind me to review this by Friday"
* "Save this as Excel", "export the answer to CSV"
* or both at once: "Email net sales by region in 2025 to Layla" (answered
  first, then drafted)

**Emails show a chart picture** above the table when the answer has
labels and numbers (2 to 25 rows): a bar chart drawn on this computer
(Pillow), declines in red. It is shown in the email body, not as a separate
file, and can be switched off on the card ("Include a chart picture"). The
card's preview shows it.

Or use the **buttons under the latest data answer**: **Email**, **Excel**,
**Reminder** (**To Do task** when tasks go to To Do) and **Teams**. Only
the kinds switched on in Admin → Actions are shown, and only to users with
the Actions permission. A button opens the same draft card as a typed
request. Email starts with no recipient (choose one on the card), Excel
can be switched to CSV, and the reminder has no due date until you set
one.

The assistant drafts the action on a card: recipients, subject, your note,
the attachment, the task list, with a preview of exactly what will be sent.
Fields can be edited. **Nothing happens until you click Approve**, and each
draft runs once. A draft expires after 15 minutes or when you ask a new
question. Recipients come only from the address book, the allowed email
domains, or yourself. The message is built by the app from the checked
answer, never written by the model, and text inside the data can never
start an action. Every draft, approval and result is listed in
Admin → Actions → History (`logs/actions.jsonl`).

Actions are **off by default**. To turn them on: Admin → Actions → Enable,
fill the address book and allowed domains. Export works straight away
(files go to `exports\`). Teams needs a Workflows webhook address for the
channel (Teams: channel ⋯ → Workflows → *Post to a channel when a webhook
request is received*).

Email can go out two ways (Admin → Actions → Email):

* **My own email account (SMTP)**, for example Gmail: enter the address and
  an *app password* (Google Account → Security → 2-Step Verification → App
  passwords; not your normal password). It is stored encrypted. "Send a test
  email to myself" checks it. "Email this to me" goes to this account.
* **Microsoft 365 mailbox (Graph)**: needs `Mail.Send` (delegated, admin
  consent) on the app registration **and an Exchange Online mailbox** on
  the signed-in account; a Power BI-only account has none.

Tasks are either **calendar reminders** (an `.ics` file saved with the
exports; open it to add the reminders to Outlook, Google or Apple Calendar)
or **Microsoft To Do** (`Tasks.ReadWrite` and an Exchange mailbox). Design
and rules: `docs/AGENT_ACTIONS_PLAN.md`.

## Alerts

The bell next to the gear (Admin page permission) opens **Alerts**: a data
question checked every day at a set time, with an email when the answer
meets a condition.

* **An alert** has:
  * a name;
  * a question ("Target achievement % by store this month");
  * a condition ("below 80%", "above 100000", "at least 95%"; empty means
    send the answer every time, as a scheduled report);
  * a time and days (Sunday to Thursday by default);
  * recipients from the address book, or "me".
* **When it runs**, the question is answered like a chat question, read-only.
  If rows meet the condition, one email goes out with only those rows, the
  chart and (optionally) the rows as Excel. If nothing matches, nothing is
  sent.
* **Test (no email)** shows what would be sent. **Run now** checks and
  sends at once. Every check is listed under **History**
  (`logs\alerts.jsonl`); alerts are stored in `config\alerts.json`.
* **Creating the alert is the approval;** later emails go without a card.
  Each email is still checked like a chat action: address book or allowed
  domains, the sending account, and the daily email limit, which it shares
  with chat emails.
* **Checked every minute** by a small worker process
  (`scripts\alerts_worker.py`). It starts and stops with the app, so alerts
  run even when nobody has the app open. If the computer is off at an
  alert's time, the alert runs when the app is next running that day. The
  Alerts page warns if the worker is not running; its output is in
  `logs\alerts_worker.log`.

## Evaluation dashboard

The chart button next to the gear (or <http://localhost:8501/?view=evaluation>)
shows how often each assistant answers correctly on its fixed question set:
headline scores, pass rate by question group (Power BI) or from search to
cited answer (Documents), the trend across runs, plain-language insights,
and the failed questions. **Rerun evaluation** runs a set again in the
background with the current settings (several minutes, one at a time) and
adds the result as a new run. Methods and caveats: `docs/EVALUATION.md`.

## Admin settings

The gear button next to "New chat" (or <http://localhost:8501/?view=admin>)
opens the Admin page. Saved changes apply to the next question; no restart
is needed.

| Tab | What you can change |
|---|---|
| **Models** | Which assistant answers; the planner model and, separately, the verifier model. Each can be a local Ollama model, any OpenAI-compatible API (OpenAI, OpenRouter, Groq, Gemini, LM Studio ...) or Anthropic Claude. Per model: name, service address, API key, timeout, output-token limit, temperature, top-p, seed, context window and keep-loaded time (Ollama), JSON mode, token-limit parameter and reasoning effort (OpenAI-compatible), effort and refusal fallback (Claude). A Test button sends one tiny request. |
| **Answer checks** | Refuse data-changing requests, the one-time repair turn, the answerability check and its two triggers, what happens when the verifier's reply is unusable, rows shown per answer. |
| **Power BI** | Dataset, workspace, sign-in account, the report and page shown on the right, timeout. |
| **Interface** | Starter questions, chat panel height, whether the query plan and DAX are shown, whether a chart of each answer is added to the Power BI report. |
| **Voice** | Auto-send and speech model (used once voice input is installed). |
| **Documents** | Everything on the Documents page: PDF folder and subfolders; search and answer models and Ollama address; answer temperature, context window, keep-loaded time, timeout, the answer instructions (system prompt) and the not-found reply; passages per answer, candidates, minimum relevance, keyword search, fusion constant; follow-up memory; passage size and overlap, minimum text per page, header/footer detection, embedding batch and timeout, indexing on page open; example questions, passages and PDF buttons under answers, read aloud and its speed. Settings that change the index rebuild it automatically. |

Things to know:

* Settings are saved in `config\settings.json`. API keys are saved
  separately in `config\secrets.bin`, encrypted for your Windows account.
  Neither file is committed.
* The accuracy measured in `docs/EVALUATION.md` applies only to the default
  settings. The page tells you when you have moved away from them, and
  "Reset all to defaults" brings them back.
* With a cloud model, each question and the data model's table, column and
  measure names (plus sample column values) are sent to that provider and
  billed per use. Query results are never sent to any model.
* A chat subscription (ChatGPT Plus, Claude Pro) does not include API
  access; you need an API key from the provider's developer console.
* The page has no password. The app is only reachable from this computer.

## Asking by voice

Click the microphone in the question box, ask your question, and stop the
recording. InsightPilot writes what it heard into the box; check it and
press Enter. The first time, the browser asks for permission to use the
microphone.

Speech is turned into text on this computer (whisper.cpp, English). The
first use downloads a 141 MB model into `models\`. To send spoken
questions without the review step, switch it on under Admin settings
(gear button) → Voice.

## One-time setup

In PowerShell, from the project folder:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\Install-InsightPilot.ps1
```

This registers a scheduled task named `InsightPilot` that starts the server
hidden at every sign-in and restarts it if it exits, creates the shortcuts
above, and starts the server. Run it again if you move or rename the project
folder. `scripts\Uninstall-InsightPilot.ps1` removes the task and shortcuts.

Requirements: the `.venv` Python environment, [Ollama](https://ollama.com)
with `qwen2.5-coder:7b`, and the `ENTRA_TENANT_ID` / `ENTRA_CLIENT_ID`
environment variables for Power BI sign-in.

## Why a sign-in task and not IIS or a Windows service

* Power BI access uses your own sign-in, stored encrypted for your Windows
  account. A service or IIS application pool runs as a different account and
  cannot read it.
* The sign-in redirect is registered for `http://localhost:8501`, which is
  the address the app already serves.
* The app is a Streamlit (Python, WebSocket) server. IIS could only sit in
  front of it as a reverse proxy, and something would still have to keep the
  Python process alive.

## Troubleshooting

* **Two Microsoft accounts.** The report panel always opens Microsoft's
  account chooser, with the account the app's answers use (Admin → Power BI
  → Sign-in account, or the saved sign-in) suggested. After loading, the
  panel says which account it is signed in as and warns if it differs.
  "Use a different account" next to the status reopens the chooser.
* Logs are in `logs\`: `server.log` (starts, stops, restarts),
  `app.err.log` and `app.out.log` (the app itself).
* "Local model offline" in the header means Ollama is not responding. The
  server tries to start it; otherwise start Ollama and ask again.
* Agent selection: the background server uses the local structured pipeline
  (`INSIGHTPILOT_AGENT=structured`). Running `python -m streamlit run
  src/main.py` by hand without that variable uses the original Azure AI
  Foundry agent.

## Development

```powershell
.venv\Scripts\python.exe -m pytest tests -q
```

Evaluation method and measured results: [`docs/EVALUATION.md`](docs/EVALUATION.md).
