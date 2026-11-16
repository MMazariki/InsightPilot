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

## Admin settings

The gear button next to "New chat" (or <http://localhost:8501/?view=admin>)
opens the Admin page. Saved changes apply to the next question; no restart
is needed.

| Tab | What you can change |
|---|---|
| **Models** | Which assistant answers; the planner model and, separately, the verifier model. Each can be a local Ollama model, any OpenAI-compatible API (OpenAI, OpenRouter, Groq, Gemini, LM Studio ...) or Anthropic Claude. Per model: name, service address, API key, timeout, output-token limit, temperature, top-p, seed, context window and keep-loaded time (Ollama), JSON mode, token-limit parameter and reasoning effort (OpenAI-compatible), effort and refusal fallback (Claude). A Test button sends one tiny request. |
| **Answer checks** | Refuse data-changing requests, the one-time repair turn, the answerability check and its two triggers, what happens when the verifier's reply is unusable, rows shown per answer. |
| **Power BI** | Dataset, workspace, sign-in account, the report and page shown on the right, timeout. |
| **Interface** | Starter questions, chat panel height, whether the query plan and DAX are shown. |
| **Voice** | Auto-send and speech model (used once voice input is installed). |

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
