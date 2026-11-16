import base64
import os
import uuid
from pathlib import Path

import requests
import streamlit as st

from powerbi_embed_component import powerbi_embed_component
from src import voice
from src import admin_page, agent_factory
from src import settings as app_settings


APP_NAME = "InsightPilot"
ASSETS = Path(__file__).resolve().parents[1] / "assets"
ICON_FILE = ASSETS / "insightpilot.png"

# Agent selection. "foundry" (default) keeps the original Azure AI Foundry
# agent; "structured" uses the local structured-planning pipeline
# (src/structured_agent.py: local Ollama + validated plans + read-only DAX).
AGENT_MODE = os.environ.get("INSIGHTPILOT_AGENT", "foundry").strip().lower()

# Admin settings (src/settings.py). Read on every run, so a saved change
# applies to the next question without restarting the server.
SETTINGS = app_settings.load()
if SETTINGS["agent"]["mode"] != "launcher":
    AGENT_MODE = SETTINGS["agent"]["mode"]

# Voice input (src/voice.py, when installed) reads these.
VOICE_INSTALLED = Path(__file__).with_name("voice.py").exists()
os.environ["INSIGHTPILOT_VOICE_AUTOSEND"] = (
    "1" if SETTINGS["voice"]["autosend"] else "0")
os.environ["INSIGHTPILOT_VOICE_MODEL"] = SETTINGS["voice"]["model"]

DETAILS_MARKER = "**Query plan and DAX**"
FOOTER_MARKER = "_Structured pipeline"

STARTER_QUESTIONS = SETTINGS["interface"]["starter_questions"]


def create_agent():
    if AGENT_MODE == "structured":
        from src.structured_agent import StructuredPlanningAgent

        return StructuredPlanningAgent(
            **agent_factory.structured_agent_kwargs(SETTINGS))

    from src.powerbi_agent import PowerBIAgent

    agent_factory.apply_foundry_overrides(SETTINGS)
    return PowerBIAgent()


st.set_page_config(
    page_title=APP_NAME,
    page_icon=str(ICON_FILE) if ICON_FILE.exists() else "🧭",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# =====================================================
# STYLE
# =====================================================

st.html(
    """
<style>
:root {
    --ip-ink: #101828;
    --ip-muted: #667085;
    --ip-line: #E4E7EC;
    --ip-surface: #FFFFFF;
    --ip-canvas: #F4F6FB;
    --ip-navy: #0B1F4B;
    --ip-blue: #2251D1;
    --ip-teal: #14B8A6;
}

/* Page chrome */
[data-testid="stHeader"], [data-testid="stToolbar"], #MainMenu, footer {
    display: none !important;
}
[data-testid="stAppViewContainer"], .stApp { background: var(--ip-canvas); }
.block-container {
    padding: 1.1rem 1.6rem 1.2rem 1.6rem;
    max-width: 1720px;
}
html, body, .stApp, .stMarkdown, button, input, textarea {
    font-family: "Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system,
        "Helvetica Neue", Arial, sans-serif;
}

/* Brand bar */
.ip-brand {
    display: flex; align-items: center; justify-content: space-between;
    gap: 16px; flex-wrap: wrap;
    padding: 16px 22px;
    border-radius: 16px;
    background: linear-gradient(110deg, #0B1F4B 0%, #16337E 55%, #2251D1 100%);
    color: #fff;
    box-shadow: 0 10px 30px -14px rgba(11, 31, 75, .55);
}
.ip-brand-left { display: flex; align-items: center; gap: 14px; min-width: 0; }
.ip-logo {
    flex: none; border-radius: 22%;
    box-shadow: 0 0 0 1px rgba(255, 255, 255, .28), 0 6px 16px -6px rgba(0, 0, 0, .5);
}
.ip-welcome-logo { display: inline-block; box-shadow: 0 10px 24px -10px rgba(11, 31, 75, .6); border-radius: 22%; }
.ip-name {
    font-size: 1.45rem; font-weight: 700; letter-spacing: -.01em; line-height: 1.1;
}
.ip-name span { color: #5EEAD4; }
.ip-tag { font-size: .86rem; color: rgba(255, 255, 255, .74); margin-top: 3px; }
.ip-pills { display: flex; gap: 8px; flex-wrap: wrap; }
.ip-pill {
    display: inline-flex; align-items: center; gap: 7px;
    padding: 6px 12px; border-radius: 999px;
    font-size: .8rem; font-weight: 500; white-space: nowrap;
    background: rgba(255, 255, 255, .1);
    border: 1px solid rgba(255, 255, 255, .16);
    color: rgba(255, 255, 255, .92);
}
.ip-dot { width: 8px; height: 8px; border-radius: 50%; background: #98A2B3; }
.ip-dot.ok { background: #34D399; box-shadow: 0 0 0 3px rgba(52, 211, 153, .22); }
.ip-dot.bad { background: #F97066; box-shadow: 0 0 0 3px rgba(249, 112, 102, .22); }

/* Cards */
.st-key-chat_card, .st-key-report_card, .st-key-admin_card {
    background: var(--ip-surface);
    border: 1px solid var(--ip-line) !important;
    border-radius: 16px !important;
    padding: 18px 20px 16px 20px;
    box-shadow: 0 1px 2px rgba(16, 24, 40, .04), 0 8px 24px -18px rgba(16, 24, 40, .25);
}
.ip-card-title {
    font-size: 1.05rem; font-weight: 650; color: var(--ip-ink); line-height: 1.2;
}
.ip-card-sub { font-size: .84rem; color: var(--ip-muted); margin-top: 2px; }

/* Conversation area */
.st-key-chat_scroll {
    border: none !important; background: transparent; padding: 0 !important;
}
[data-testid="stChatMessage"] {
    background: transparent; padding: .55rem .2rem; gap: .7rem;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
    background: #EEF3FF; border-radius: 14px; padding: .65rem .9rem;
}
[data-testid="stChatMessage"] table {
    font-size: .88rem; border-collapse: separate; border-spacing: 0;
    border: 1px solid var(--ip-line); border-radius: 10px; overflow: hidden;
    font-variant-numeric: tabular-nums;
}
[data-testid="stChatMessage"] th {
    background: #F2F4F8; color: #344054; font-weight: 600; text-align: left;
    border: none !important; border-bottom: 1px solid var(--ip-line) !important;
}
[data-testid="stChatMessage"] td {
    border: none !important; border-bottom: 1px solid #F0F2F5 !important;
}
[data-testid="stChatMessage"] tr:last-child td { border-bottom: none !important; }
[data-testid="stChatMessage"] td:not(:first-child),
[data-testid="stChatMessage"] th:not(:first-child) { text-align: right; }

/* Welcome */
.ip-welcome { text-align: center; padding: 34px 12px 6px 12px; }
.ip-welcome h3 {
    font-size: 1.3rem; font-weight: 650; color: var(--ip-ink);
    margin: 12px 0 4px 0; padding: 0;
}
.ip-welcome p { color: var(--ip-muted); font-size: .93rem; margin: 0; }
.ip-try {
    font-size: .74rem; font-weight: 600; letter-spacing: .08em;
    text-transform: uppercase; color: #98A2B3; margin: 22px 0 2px 2px;
}
.st-key-starters button {
    width: 100%; min-height: 58px; text-align: left; justify-content: flex-start;
    border-radius: 12px; border: 1px solid var(--ip-line);
    background: #FAFBFD; color: var(--ip-ink); font-weight: 500;
    transition: border-color .15s, background .15s, transform .15s;
}
.st-key-starters button:hover {
    border-color: var(--ip-blue); background: #F1F5FF; color: var(--ip-blue);
    transform: translateY(-1px);
}
.st-key-starters button p { font-size: .9rem; }

/* Buttons and input */
.st-key-open_admin button {
    border-radius: 10px; border: 1px solid var(--ip-line);
    color: #475467; background: #fff; padding-left: 0; padding-right: 0;
}
.st-key-open_admin button:hover { border-color: var(--ip-blue); color: var(--ip-blue); }
.st-key-new_chat button {
    border-radius: 10px; border: 1px solid var(--ip-line);
    color: #344054; font-weight: 500; background: #fff;
}
.st-key-new_chat button:hover { border-color: var(--ip-blue); color: var(--ip-blue); }
[data-testid="stChatInput"] {
    border-radius: 14px; border: 1px solid #D0D5DD; background: #fff;
}
[data-testid="stChatInput"] > div,
[data-testid="stChatInput"] textarea { background: #fff !important; }
[data-testid="stChatInput"] > div { border: none !important; border-radius: 14px; }
[data-testid="stChatInput"]:focus-within {
    border-color: var(--ip-blue); box-shadow: 0 0 0 3px rgba(34, 81, 209, .14);
}
[data-testid="stExpander"] details {
    border-radius: 10px; border-color: var(--ip-line); background: #FAFBFD;
}
[data-testid="stExpander"] summary p { font-size: .85rem; color: #475467; }
.st-key-admin_card h3 { font-size: 1.08rem; font-weight: 650; padding-top: .9rem; }
.ip-foot { text-align: center; color: #98A2B3; font-size: .76rem; margin-top: 10px; }
</style>
"""
)

def _logo_html(size):
    if not ICON_FILE.exists():
        return ""
    encoded = base64.b64encode(ICON_FILE.read_bytes()).decode("ascii")
    return (
        f'<img src="data:image/png;base64,{encoded}" width="{size}" '
        f'height="{size}" alt="" style="display:block;border-radius:22%;">'
    )


LOGO_SMALL = _logo_html(46)
LOGO_LARGE = _logo_html(60)


# =====================================================
# SESSION STATE
# =====================================================

if "messages" not in st.session_state:
    st.session_state.messages = []

if "agent" not in st.session_state:
    st.session_state.agent = None
if "visual_request" not in st.session_state:
    st.session_state.visual_request = None
if "queued_prompt" not in st.session_state:
    st.session_state.queued_prompt = None
if "voice_draft" not in st.session_state:
    st.session_state.voice_draft = None
if "voice_notice" not in st.session_state:
    st.session_state.voice_notice = None

# The microphone button is shown only when speech-to-text is installed.
VOICE_ENABLED = voice.is_available()


@st.cache_resource(show_spinner=False)
def get_transcriber():
    return voice.Transcriber()
if "view" not in st.session_state:
    st.session_state.view = (
        "admin" if st.query_params.get("view") == "admin" else "chat")

# Rebuild the agent when the saved settings or API keys change.
SETTINGS_STAMP = app_settings.stamp()
if st.session_state.get("settings_stamp") != SETTINGS_STAMP:
    if st.session_state.agent is not None:
        try:
            st.session_state.agent.close()
        except Exception:
            pass
        st.session_state.agent = None
    st.session_state.settings_stamp = SETTINGS_STAMP


def queue_prompt(question):
    st.session_state.queued_prompt = question


def reset_conversation():
    st.session_state.messages = []
    st.session_state.visual_request = None


def open_admin():
    st.session_state.view = "admin"


@st.cache_data(ttl=20, show_spinner=False)
def local_model_online(base_url):
    try:
        return requests.get(
            base_url.rstrip("/") + "/api/tags", timeout=1.5
        ).ok
    except requests.RequestException:
        return False


def render_answer(content):
    """Show an assistant message; the query plan and DAX go in an expander."""

    if DETAILS_MARKER not in content:
        st.markdown(content)
        return

    body, rest = content.split(DETAILS_MARKER, 1)
    details, separator, footer = rest.rpartition(FOOTER_MARKER)
    if not separator:
        details, footer = rest, ""

    st.markdown(body.strip())
    if not SETTINGS["interface"]["show_query_details"]:
        return
    with st.expander("How this was answered: query plan and DAX"):
        st.markdown(details.strip())
        if footer:
            st.caption(("Structured pipeline" + footer).strip().rstrip("_"))


# =====================================================
# APPLICATION HEADER
# =====================================================

if AGENT_MODE == "structured":
    from src.structured_agent import PIPELINE_VERSION

    planner_settings = SETTINGS["planner"]
    model_name = planner_settings["model"]
    if planner_settings["provider"] == "ollama":
        online = local_model_online(planner_settings["base_url"])
        model_pill = (
            f'<span class="ip-pill"><span class="ip-dot {"ok" if online else "bad"}">'
            f'</span>Local model {"online" if online else "offline"} · {model_name}</span>'
        )
    else:
        online = True
        model_pill = (
            '<span class="ip-pill"><span class="ip-dot ok"></span>'
            f'Cloud model · {model_name}</span>'
        )
    pills = (
        model_pill
        + f'<span class="ip-pill">Pipeline {PIPELINE_VERSION}'
        + ("" if SETTINGS["pipeline"] == app_settings.DEFAULTS["pipeline"]
           else " · custom checks")
        + '</span>'
        '<span class="ip-pill">Read-only Power BI</span>'
    )
    tagline = "Ask your sales data in plain language. Answers come from validated, read-only queries."
else:
    online = True
    pills = (
        '<span class="ip-pill">Azure AI Foundry agent</span>'
        '<span class="ip-pill">Power BI</span>'
    )
    tagline = "AI-powered business intelligence on your Power BI model."

st.html(
    f"""
<div class="ip-brand">
  <div class="ip-brand-left">
    <div class="ip-logo">{LOGO_SMALL}</div>
    <div>
      <div class="ip-name">Insight<span>Pilot</span></div>
      <div class="ip-tag">{tagline}</div>
    </div>
  </div>
  <div class="ip-pills">{pills}</div>
</div>
"""
)

if not online:
    st.warning(
        "The local model service (Ollama) is not responding, so questions "
        "cannot be answered yet. Start Ollama, or use the Restart "
        "InsightPilot shortcut, then ask again.",
        icon=":material/cloud_off:",
    )


# =====================================================
# ADMIN PAGE
# =====================================================

if st.session_state.view == "admin":
    with st.container(border=True, key="admin_card"):
        settings_saved = admin_page.render(AGENT_MODE, VOICE_INSTALLED)
    if settings_saved:
        st.rerun()
    st.stop()


# =====================================================
# MAIN LAYOUT
# =====================================================

chat_column, report_column = st.columns(
    [5, 6],
    gap="medium",
)


# =====================================================
# LEFT PANEL - BI AGENT
# =====================================================

with chat_column, st.container(border=True, key="chat_card"):

    title_column, action_column, admin_column = st.columns(
        [3, 1, 0.34], vertical_alignment="center")
    with title_column:
        st.html(
            '<div class="ip-card-title">Ask InsightPilot</div>'
            '<div class="ip-card-sub">Questions about sales, profit, units, '
            "discounts and targets.</div>"
        )
    with action_column:
        st.button(
            "New chat",
            key="new_chat",
            icon=":material/add_comment:",
            on_click=reset_conversation,
            disabled=not st.session_state.messages,
            use_container_width=True,
        )
    with admin_column:
        st.button(
            "",
            key="open_admin",
            icon=":material/settings:",
            help="Admin settings: models, checks, Power BI, interface",
            on_click=open_admin,
            use_container_width=True,
        )

    chat_container = st.container(
        height=SETTINGS["interface"]["chat_height"], border=False,
        key="chat_scroll")

    notice_slot = st.empty()
    if st.session_state.voice_notice:
        notice = st.session_state.voice_notice
        st.session_state.voice_notice = None
        if notice["kind"] == "error":
            notice_slot.warning(notice["text"], icon=":material/mic_off:")
        else:
            notice_slot.info(notice["text"], icon=":material/mic:")

    # A heard question waiting for review goes into the input box. This
    # must be set before the widget is created in this run.
    if st.session_state.voice_draft:
        st.session_state["agent_chat_input"] = st.session_state.voice_draft
        st.session_state.voice_draft = None

    if VOICE_ENABLED:
        submission = st.chat_input(
            "Ask a business question, or use the microphone...",
            key="agent_chat_input",
            accept_audio=True,
            audio_sample_rate=voice.SAMPLE_RATE,
        )
    else:
        submission = st.chat_input(
            "Ask a business question...",
            key="agent_chat_input",
        )

    # With the microphone on, even typed questions arrive as an object
    # with .text and .audio, not as a plain string.
    typed_text, audio_bytes = voice.split_submission(submission)
    prompt = typed_text or None

    if audio_bytes:
        heard = ""
        with chat_container, st.spinner("Transcribing your voice..."):
            try:
                heard = voice.transcribe_wav(audio_bytes, get_transcriber())
            except voice.VoiceError as exc:
                st.session_state.voice_notice = {
                    "kind": "error", "text": str(exc)}
        combined = " ".join(part for part in (typed_text, heard) if part)
        if heard and voice.autosend_enabled():
            prompt = combined
        else:
            if heard:
                st.session_state.voice_notice = {
                    "kind": "info",
                    "text": "Check the question in the box, then press "
                            "Enter to send it.",
                }
            st.session_state.voice_draft = combined or None
            st.rerun()

    if st.session_state.queued_prompt:
        prompt = st.session_state.queued_prompt
        st.session_state.queued_prompt = None

    with chat_container:

        if not st.session_state.messages and not prompt:

            st.html(
                f"""
<div class="ip-welcome">
  <div class="ip-welcome-logo">{LOGO_LARGE}</div>
  <h3>What would you like to know?</h3>
  <p>Ask in your own words. InsightPilot plans a query, checks it,
     and reads the answer from Power BI.</p>
</div>
{'<div class="ip-try">Try one of these</div>' if STARTER_QUESTIONS else ''}
"""
            )
            with st.container(key="starters"):
                left, right = st.columns(2)
                for index, question in enumerate(STARTER_QUESTIONS):
                    (left if index % 2 == 0 else right).button(
                        question,
                        key=f"starter_{index}",
                        on_click=queue_prompt,
                        args=(question,),
                        use_container_width=True,
                    )

        for message in st.session_state.messages:

            if message["role"] == "user":
                with st.chat_message("user", avatar=":material/person:"):
                    st.markdown(message["content"])
            else:
                with st.chat_message("assistant", avatar=":material/explore:"):
                    render_answer(message["content"])

    if prompt:

        st.session_state.messages.append({
            "role": "user",
            "content": prompt,
        })

        with chat_container:
            with st.chat_message("user", avatar=":material/person:"):
                st.markdown(prompt)

            with st.chat_message("assistant", avatar=":material/explore:"):
                with st.spinner("Planning the query and reading Power BI..."):

                    try:

                        if st.session_state.agent is None:
                            st.session_state.agent = create_agent()

                        answer = st.session_state.agent.ask(prompt)
                        if st.session_state.agent.pending_visual_request:
                            st.session_state.visual_request = {
                                **st.session_state.agent.pending_visual_request,
                                "requestId": str(uuid.uuid4()),
                            }

                            print(
                                "[STREAMLIT] Visual request received:",
                                st.session_state.visual_request
                            )

                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": answer,
                        })

                    except Exception as exc:

                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": f"**Error:** {exc}",
                        })

        st.rerun()


# =====================================================
# RIGHT PANEL - POWER BI REPORT
# =====================================================

with report_column, st.container(border=True, key="report_card"):

    st.html(
        '<div class="ip-card-title">Power BI report</div>'
        '<div class="ip-card-sub">Live interactive report on the same '
        "semantic model.</div>"
    )

    powerbi_embed_component(
        key="main-powerbi-report",
        visual_request=st.session_state.visual_request,
        report_id=SETTINGS["powerbi"]["report_id"] or None,
        page_id=SETTINGS["powerbi"]["page_id"] or None,
    )

st.html(
    f'<div class="ip-foot">{APP_NAME} · '
    + ("questions are sent to a cloud model · "
       if AGENT_MODE != "structured" or app_settings.uses_cloud(SETTINGS)
       else "runs on this computer · ")
    + "queries are read-only</div>"
)
