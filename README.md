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
