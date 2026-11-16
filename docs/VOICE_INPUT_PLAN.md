# Plan: voice input for the InsightPilot chat box

**Status:** planned, not applied. Written 2026-11-16 for another model to
implement. Everything marked "verified" below was actually run on this
machine on that date; do not re-research it.

**How to use this plan:** do the steps in section 4 in order, exactly as
written. Run every command from the project root `L:\Projects\InsightPilot`.
Do not redesign anything. If a step fails, use section 6; if that does not
resolve it, stop and report the exact error to the user instead of
improvising.

---

## 1. What is being built

A microphone button inside the chat input box ("Ask a business question...").
The user clicks it, speaks a question, and stops recording. The app turns the
speech into text on this computer and puts the text in the input box. The
user checks it and presses Enter to send. Typed questions keep working exactly
as before.

Decisions already made (do not change):

| Decision | Choice | Reason |
|---|---|---|
| Recording | Streamlit's built-in `st.chat_input(accept_audio=True)` | Already in the installed Streamlit 1.67. No custom JavaScript. |
| Speech-to-text | `pywhispercpp` 1.5.1 (whisper.cpp), model `base.en`, CPU | Runs locally and free (project rule: no paid cloud). Only engine that installs on this Python. |
| After transcription | Text goes into the input box for review; user presses Enter | A misheard word would otherwise run a real query. |
| Auto-send option | Admin settings → Voice → "Send spoken questions immediately" | Off by default. The Admin page sets `INSIGHTPILOT_VOICE_AUTOSEND` for `voice.py`; setting that variable by hand has no effect. Verified 2026-11-16. |
| Language | English only | The planner model and the data vocabulary are English. |
| If the package is missing | Microphone button is hidden; app works as before | The app must never fail to start because of voice. |

## 2. Facts verified on this machine (do not re-check, do not work around)

Environment:

* Python is **3.15.2** in `.venv`. Windows 10, Intel i5-9400F (6 cores),
  32 GB RAM. The RTX 3050 (4 GB) is used by Ollama; **speech runs on CPU**.
* `faster-whisper` **cannot be installed** (its dependencies `onnxruntime`
  and `pyyaml` have no Python 3.15 build). `openai-whisper` cannot either.
  **Do not try them.** `pywhispercpp` installs cleanly; its only dependencies
  are `numpy`, `requests`, `tqdm`, `platformdirs`.
* `pywhispercpp` downloads `ggml-base.en.bin` (141 MB) on first use into the
  folder given as `models_dir`, then works offline.
* Measured with `base.en` on this CPU: about **0.8 seconds** to transcribe a
  3-4 second question. All six test questions were transcribed correctly,
  including "Riyadh", "Jeddah", "Click & Collect" and "Q3". `small.en`
  (488 MB) takes 2.6 seconds and was not more accurate, so it is not used.
* Silence produces the text `[BLANK_AUDIO]`; noise produces text in brackets
  such as `[sound of a machine running]`. `voice.py` removes these.

Streamlit behaviour (verified with Streamlit's test harness):

* With `accept_audio=True`, `st.chat_input` returns a **`ChatInputValue`
  object, not a string, even for typed questions**. It has `.text` (string)
  and `.audio` (`None` or an uploaded WAV file with `.getvalue()`).
  `voice.split_submission` handles this. Code that treats the result as a
  string will break.
* Recorded audio arrives as WAV, 16 kHz when `audio_sample_rate=16000`.
* Setting `st.session_state["agent_chat_input"] = "some text"` **before** the
  widget is created puts that text in the box without sending it. Setting it
  after the widget is created raises an error. The edit script does it in the
  right place.
* The browser only allows the microphone on `https` or `localhost`. The app
  is served on `http://localhost:8501`, so it works. The browser asks for
  microphone permission once.

Already tested in a scratch copy of the app (so these are expected results,
not hopes): typed question with the microphone off; typed question with the
microphone on; spoken question placed in the box for review; spoken question
with auto-send; silence showing a warning and sending nothing.

**Not tested, needs a human:** a real microphone in a real browser (step 9).

## 3. Project rules the implementer must follow

* Run tests with `.venv\Scripts\python.exe -m pytest tests -q`. Do **not**
  use `pytest.exe` directly (imports fail). Baseline before this work:
  **613 passed**.
* Do not edit anything under `evaluation/`, and never commit
  `evaluation/results/`.
* Do not edit `src/structured_agent.py`, `src/powerbi_agent.py`,
  `src/settings.py`, `src/llm.py`, `src/admin_page.py`,
  `src/agent_factory.py` or the `powerbi_embed_component/` folder.
* `src/main.py` already contains an Admin page (gear button). The edit
  script was re-checked against that version on 2026-11-16 and applies
  cleanly; the Admin page's Voice tab starts working by itself once
  `src/voice.py` exists.
* The app runs permanently in the background (scheduled task `InsightPilot`)
  on port 8501. Python modules are loaded once, so **the app must be
  restarted** to pick up the new package (step 8).
* PowerShell script execution is restricted on this machine. Always run
  scripts as `powershell -ExecutionPolicy Bypass -File <script>`.
* Committing and pushing directly to `main` is approved by the user.
* `INSIGHTPILOT_CLAUDE_HANDOFF.md` stays untracked. Do not `git add` it.
* Do not use `git add -A` or `git add .`; add the listed files by name.

## 4. Steps

The tested files are already in `docs/voice_input/`:

| File | Becomes |
|---|---|
| `docs/voice_input/voice.py` | `src/voice.py` (new module, 20 tests pass) |
| `docs/voice_input/test_voice.py` | `tests/test_voice.py` |
| `docs/voice_input/apply_main_edits.py` | script that edits `src/main.py` |
| `docs/voice_input/sample_show_gross_profit_by_sales_channel.wav` | spoken test question for step 6 |

### Step 1. Confirm the starting point

```powershell
git status --short
.venv\Scripts\python.exe -m pytest tests -q
```

Expected: only `?? INSIGHTPILOT_CLAUDE_HANDOFF.md` in git status, and
`613 passed`. If tests fail before you change anything, stop and report.

### Step 2. Install the speech package

```powershell
.venv\Scripts\python.exe -m pip install --only-binary=:all: "pywhispercpp>=1.5,<2"
.venv\Scripts\python.exe -c "from pywhispercpp.model import Model; print('ok')"
```

Expected: `ok`.

### Step 3. Copy the two tested files into place

```powershell
Copy-Item docs\voice_input\voice.py src\voice.py
Copy-Item docs\voice_input\test_voice.py tests\test_voice.py
```

Do not edit them.

### Step 4. Edit `src/main.py` with the script

```powershell
.venv\Scripts\python.exe docs\voice_input\apply_main_edits.py
```

Expected: `Applied 3 edits to src\main.py.` The script makes three changes:
imports `voice`; adds session state, the `VOICE_ENABLED` switch and a cached
`get_transcriber()`; and replaces the `st.chat_input(...)` call with the
microphone-enabled version plus the transcription logic. Do not hand-edit
`src/main.py` unless the script fails (section 6).

### Step 5. Small file updates

1. `.gitignore`: add these two lines at the end of the file:

   ```
   # Voice model files (downloaded on first use)
   models/
   ```

2. `requirements.txt`: add one line at the end:

   ```
   pywhispercpp>=1.5,<2
   ```

3. `README.md`: add this section immediately **before** the line
   `## One-time setup`:

   ```markdown
   ## Asking by voice

   Click the microphone in the question box, ask your question, and stop the
   recording. InsightPilot writes what it heard into the box; check it and
   press Enter. The first time, the browser asks for permission to use the
   microphone.

   Speech is turned into text on this computer (whisper.cpp, English). The
   first use downloads a 141 MB model into `models\`. To send spoken
   questions without the review step, switch it on under Admin settings
   (gear button) → Voice.
   ```

   Also in `README.md`, in the "Requirements:" paragraph under "One-time
   setup", no change is needed.

### Step 6. Download the model and check real transcription

```powershell
.venv\Scripts\python.exe -c "from src import voice; t = voice.Transcriber(); print(voice.transcribe_wav(open('docs/voice_input/sample_show_gross_profit_by_sales_channel.wav','rb').read(), t))"
```

Expected (the first run downloads 141 MB, then prints):
`Show Gross Profit by Sales Channel.` Capital letters may differ; the words
must match. Afterwards `models\ggml-base.en.bin` exists.

### Step 7. Run the tests

```powershell
.venv\Scripts\python.exe -m pytest tests -q
```

Expected: **633 passed** (613 + 20 new).

### Step 8. Restart the app

```powershell
powershell -ExecutionPolicy Bypass -File scripts\Stop-InsightPilot.ps1 -Quiet
powershell -ExecutionPolicy Bypass -Command ". .\scripts\InsightPilot-Common.ps1; Start-Sleep 2; Start-IPServer; Wait-IPRunning 90"
```

Expected: the last command prints `True`. Then check the app's error log is
clean:

```powershell
Get-Content logs\app.err.log -Tail 20
```

Expected: only a line like `Uvicorn server started on localhost:8501`. A
Python traceback here means something is wrong; see section 6.

Do **not** run `scripts\Start-InsightPilot.ps1` yourself; it opens a browser
window on the user's screen.

### Step 9. Ask the user to test with a real microphone

You cannot test a microphone. Tell the user exactly this:

> Open InsightPilot, click the microphone in the question box, allow the
> microphone if the browser asks, say "Show gross profit by sales channel",
> and stop the recording. The sentence should appear in the box. Press Enter.

Report what was and was not verified. Do not claim the microphone works
until the user confirms it.

### Step 10. Commit and push

```powershell
git add .gitignore README.md requirements.txt src/main.py src/voice.py tests/test_voice.py
git status --short
git commit -m "Add voice input to the chat box (local whisper.cpp)" -m "Microphone button via st.chat_input(accept_audio=True); speech is transcribed locally with pywhispercpp (base.en) and placed in the input box for review. INSIGHTPILOT_VOICE_AUTOSEND=1 sends immediately. The button is hidden if the package is missing."
git push origin main
```

Before committing, `git status --short` must **not** list `models/` or
`INSIGHTPILOT_CLAUDE_HANDOFF.md` as staged. Leave `docs/voice_input/` and
this plan in the repository; they are already committed.

## 5. Definition of done

* [ ] `633 passed`.
* [ ] Step 6 printed the expected sentence.
* [ ] The app restarted and `logs\app.err.log` has no traceback.
* [ ] Changes committed and pushed; `git status --short` shows only the
      untracked handoff file.
* [ ] The user was given the step 9 instructions, and the final message says
      clearly that the real-microphone test is theirs to do.

## 6. If something goes wrong

| Symptom | Cause | What to do |
|---|---|---|
| Step 2: "No matching distribution" for `pywhispercpp` | No internet, or PyPI unreachable | Stop and tell the user. Do not switch to another speech package. |
| Step 4: "Edit N expected exactly 1 match" | `src/main.py` changed since the plan was written | See "If the edit script fails" below. |
| Step 4: "already has the voice edits" | The script already ran | Continue with step 5. |
| Step 6: "The voice model could not be loaded" | The 141 MB download failed | Check internet and run step 6 again. A partial `models\ggml-base.en.bin` must be deleted first. |
| Step 7: a test in `tests/test_voice.py` fails | `src/voice.py` or the test file was altered | Copy both again from `docs/voice_input/` (step 3). |
| Step 7: `test_main_selects_agent_by_environment` fails | `src/main.py` lost a required line | `src/main.py` must still contain `os.environ.get("INSIGHTPILOT_AGENT", "foundry")` and `powerbi_embed_component(`. Restore with `git checkout src/main.py` and redo step 4. |
| Step 8 prints `False` | The app did not come up within 90 seconds | Read `logs\app.err.log` and `logs\server.log`. The server waits 30 seconds before retrying after a failed start. |
| Traceback mentioning `agent_chat_input` cannot be modified | The session-state line ran after the widget was created | The edit was applied by hand in the wrong order. `git checkout src/main.py`, redo step 4 with the script. |
| No microphone button in the app after restart | `voice.is_available()` is false in the running app | The package was installed into a different Python. Step 2 must use `.venv\Scripts\python.exe`. Then restart again (step 8). |
| User: browser never asks for the microphone, or recording is silent | Browser or Windows microphone permission | Windows Settings → Privacy → Microphone must allow desktop apps; in the browser, the padlock/site-settings for `localhost:8501` must allow the microphone. Not a code problem. |
| User: words are often misheard | Model too small for their voice or microphone | In Admin settings → Voice, set the speech model to `small.en` (488 MB download, about 2.6 seconds per question), save, and restart InsightPilot. Do not change code. |

### If the edit script fails

Do the three edits by hand in `src/main.py`. The exact old and new text for
each is in the `EDITS` list in `docs/voice_input/apply_main_edits.py`
(each entry is `(old text, new text)`). Find the place where the old text
should be, compare it with the current file, and apply the same change. The
essential rules:

1. `from src import voice` goes with the other imports at the top.
2. The block that sets `st.session_state["agent_chat_input"]` from
   `voice_draft` must run **before** `st.chat_input(...)`.
3. The result of `st.chat_input(...)` must go through
   `voice.split_submission(...)`; `prompt` must end up a plain string or
   `None`.
4. The two lines that follow the chat input in the current file must stay
   after the new block, unchanged:

   ```python
       if st.session_state.queued_prompt:
           prompt = st.session_state.queued_prompt
           st.session_state.queued_prompt = None
   ```

Then run `.venv\Scripts\python.exe -m py_compile src\main.py` and continue
with step 5.

## 7. How the finished feature works (for reference)

```
microphone button (browser, built into st.chat_input)
   -> WAV, 16 kHz, uploaded to the app
   -> voice.split_submission()      text and/or audio bytes
   -> voice.decode_wav()            mono float32; rejects < 0.4 s or > 30 s
   -> voice.Transcriber.transcribe()  whisper.cpp base.en on CPU, with a
                                      vocabulary hint built from the measure
                                      names and common values in evaluation/
   -> voice.clean_transcript()      removes [BLANK_AUDIO] and similar
   -> review mode (default): text is placed in the input box, a hint says
      "Check the question in the box, then press Enter to send it."
      auto-send mode: the text is sent as the question immediately
   -> from here the question follows the existing path unchanged
```

Settings. Since the Admin page was added (2026-11-16), `src/main.py` sets
these two environment variables from Admin settings → Voice on every run, so
the Admin page is where the user changes them; `voice.py` still just reads
the environment and needs no change:

| Variable | Default | Meaning |
|---|---|---|
| `INSIGHTPILOT_VOICE_AUTOSEND` | off | `1` sends the heard question without review |
| `INSIGHTPILOT_VOICE_MODEL` | `base.en` | whisper.cpp model name, e.g. `small.en` |

## 8. Out of scope

Do not add any of these: Arabic or other languages, spoken answers
(text-to-speech), a wake word, continuous listening, GPU acceleration,
changes to the Power BI panel, or restyling of the microphone button beyond
what Streamlit provides. If the microphone button looks out of place next to
the send arrow, report it to the user with a description instead of changing
the CSS.
