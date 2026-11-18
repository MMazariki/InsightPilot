import base64
import html
import os
import uuid
from pathlib import Path

import requests
import streamlit as st

from powerbi_embed_component import powerbi_embed_component
from src import voice
from src import action_card, admin_page, agent_factory, report_pages
from src.security import login as auth
from src.security import users as auth_users
from src.actions import detect as action_detect
import powerbi_embed_component as report_component
from src import settings as app_settings
from src.visual_builder import chart_choices, with_chart_type


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
.ip-pill.ip-user {
    background: rgba(94, 234, 212, .16);
    border-color: rgba(94, 234, 212, .45);
    color: #fff; font-weight: 600;
}
.ip-pill.ip-user svg { color: #5EEAD4; flex: none; }
.ip-user-id { font-weight: 400; color: rgba(255, 255, 255, .72); }
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
.st-key-open_admin button, .st-key-open_evaluation button {
    border-radius: 10px; border: 1px solid var(--ip-line);
    color: #475467; background: #fff; padding-left: 0; padding-right: 0;
}
.st-key-open_admin button:hover, .st-key-open_evaluation button:hover {
    border-color: var(--ip-blue); color: var(--ip-blue); }
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
.ip-voice-hint { color: #667085; font-size: .78rem; margin: -6px 2px 0 2px; }
.ip-voice-hint b { color: #344054; }
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
if "visual_built" not in st.session_state:
    st.session_state.visual_built = None
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


action_card.init_state()

if "view" not in st.session_state:
    requested_view = st.query_params.get("view")
    st.session_state.view = (requested_view if requested_view in
                             ("admin", "documents", "evaluation", "access", "alerts")
                             else "chat")

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
    action_card.supersede()
    st.session_state.messages = []
    st.session_state.visual_request = None
    st.session_state.visual_built = None
    st.session_state.action_context = None
    st.session_state.action_drafts = {}


def change_chart_type():
    built = st.session_state.visual_built
    if not built:
        return
    label = st.session_state.get("chart_type") or "Auto"
    st.session_state.visual_request = {
        **with_chart_type(built, label), "requestId": str(uuid.uuid4())}


def open_admin():
    st.session_state.view = "admin"


def open_documents():
    st.session_state.view = "documents"


def open_evaluation():
    st.session_state.view = "evaluation"


def open_access():
    st.session_state.view = "access"


def open_alerts():
    st.session_state.view = "alerts"


@st.cache_data(ttl=20, show_spinner=False)
def local_model_online(base_url):
    try:
        return requests.get(
            base_url.rstrip("/") + "/api/tags", timeout=1.5
        ).ok
    except requests.RequestException:
        return False


@st.cache_data(ttl=600, show_spinner=False)
def current_report_page(report_id, workspace_id):
    return report_pages.resolve_page_id(report_id, workspace_id)


@st.cache_data(ttl=600, show_spinner=False)
def current_query_account(configured):
    return report_pages.query_account(configured or None)


def answer_question(question):
    """Ask the agent, add its answer to the chat. True for a data answer."""

    with st.spinner("Planning the query and reading Power BI..."):
        try:
            if st.session_state.agent is None:
                st.session_state.agent = create_agent()

            answer = st.session_state.agent.ask(question)
            action_card.remember_answer(question, st.session_state.agent)
            if st.session_state.agent.pending_visual_request:
                st.session_state.visual_request = {
                    **st.session_state.agent.pending_visual_request,
                    "requestId": str(uuid.uuid4()),
                }
                # The chart as built; the type selector redraws it.
                st.session_state.visual_built = dict(st.session_state.visual_request)
                st.session_state.chart_type = "Auto"
                print("[STREAMLIT] Visual request received:",
                      st.session_state.visual_request)

            st.session_state.messages.append({
                "role": "assistant",
                "content": answer,
                # A data answer: the action buttons go under the latest one.
                "actionable": st.session_state.action_context is not None,
                "suggestions": list(getattr(
                    st.session_state.agent, "suggestions", []) or []),
            })
        except Exception as exc:
            st.session_state.action_context = None
            st.session_state.messages.append({
                "role": "assistant",
                "content": f"**Error:** {exc}",
            })
    return bool(st.session_state.action_context)


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

if st.session_state.view == "documents":
    documents_settings = SETTINGS["documents"]
    pills = (
        f'<span class="ip-pill">Answers · {documents_settings["answer_model"]}</span>'
        f'<span class="ip-pill">Search · {documents_settings["embedding_model"]}</span>'
        '<span class="ip-pill">PDFs stay on this computer</span>'
    )
    tagline = ("Ask your PDF documents in plain language. Answers come only from "
               "the documents, with file and page.")

def brand_bar(user=None):
    """The blue ribbon; with ``user``, a box with the signed-in user."""

    user_pill = ""
    if user:
        name = html.escape(user["display_name"] or user["username"])
        login = html.escape(user["username"])
        user_pill = (
            f'<span class="ip-pill ip-user" title="Signed in as {login}">'
            '<svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true">'
            '<path fill="currentColor" d="M12 12a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9Zm0 '
            '2.2c-4.2 0-8 2.1-8 5v1.3c0 .8.6 1.5 1.5 1.5h13c.8 0 1.5-.7 1.5-1.5v-1.3c0'
            '-2.9-3.8-5-8-5Z"/></svg>'
            f'{name}' + (f' <span class="ip-user-id">· {login}</span>'
                         if name != login else "") + '</span>')
    return f"""
<div class="ip-brand">
  <div class="ip-brand-left">
    <div class="ip-logo">{LOGO_SMALL}</div>
    <div>
      <div class="ip-name">Insight<span>Pilot</span></div>
      <div class="ip-tag">{tagline}</div>
    </div>
  </div>
  <div class="ip-pills">{pills}{user_pill}</div>
</div>
"""


# Drawn before the sign-in check so the sign-in screen has the ribbon too;
# filled in again with the user's name once someone is signed in.
brand_slot = st.empty()
brand_slot.html(brand_bar())


# =====================================================
# SIGN-IN AND PERMISSIONS (src/security)
# =====================================================

# Draws the first-admin, sign-in or new-password screen and stops the run
# until someone is signed in.
CURRENT_USER = auth.require()
brand_slot.html(brand_bar(CURRENT_USER))


def allowed(permission):
    return auth_users.can(CURRENT_USER, permission)


# Each page needs one of the permissions an administrator assigns.
VIEW_PERMISSION = {"chat": "power_bi", "documents": "documents",
                   "evaluation": "evaluation", "admin": "admin_settings",
                   "access": "manage_users", "alerts": "admin_settings"}
if not allowed(VIEW_PERMISSION[st.session_state.view]):
    usable = [v for v in ("chat", "documents", "access", "admin", "evaluation", "alerts")
              if allowed(VIEW_PERMISSION[v])]
    if not usable:
        left, middle, right = st.columns([1, 1.25, 1])
        with middle, st.container(border=True):
            st.warning("Your account has no pages assigned yet. Ask an "
                       "administrator to give you a permission.",
                       icon=":material/lock:")
            auth.account_menu(CURRENT_USER, "acct_none")
        st.stop()
    st.session_state.view = usable[0]

if not online and st.session_state.view == "chat":
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
# USERS AND FOLDER ACCESS
# =====================================================

if st.session_state.view == "access":
    from src import access_page

    with st.container(border=True, key="access_card"):
        access_page.render(SETTINGS, CURRENT_USER["username"])
    st.stop()


# =====================================================
# ALERTS
# =====================================================

if st.session_state.view == "alerts":
    from src import alerts_page

    with st.container(border=True, key="alerts_card"):
        alerts_page.render(CURRENT_USER["username"])
    st.stop()


# =====================================================
# DOCUMENTS PAGE (answers from PDFs)
# =====================================================

if st.session_state.view == "documents":
    from src import documents_page

    documents_page.render(SETTINGS, CURRENT_USER)
    st.stop()


# =====================================================
# EVALUATION DASHBOARD
# =====================================================

if st.session_state.view == "evaluation":
    from src import evaluation_page

    with st.container(border=True, key="eval_page"):
        evaluation_page.render()
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

    (title_column, docs_column, action_column, eval_column, alerts_column, admin_column,
     users_column, account_column) = st.columns(
        [2.3, 1.1, 1, 0.34, 0.34, 0.34, 0.34, 0.9], vertical_alignment="center")
    with title_column:
        st.html(
            '<div class="ip-card-title">Ask InsightPilot</div>'
            '<div class="ip-card-sub">Questions about sales, profit, units, '
            "discounts and targets.</div>"
        )
    with docs_column:
        if allowed("documents"):
            st.button(
                "Documents",
                key="open_documents",
                icon=":material/description:",
                help="Ask questions answered from your PDF documents",
                on_click=open_documents,
                use_container_width=True,
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
    with eval_column:
        if allowed("evaluation"):
            st.button(
                "",
                key="open_evaluation",
                icon=":material/monitoring:",
                help="Evaluation dashboard: measured accuracy, insights and reruns",
                on_click=open_evaluation,
                use_container_width=True,
            )
    with alerts_column:
        if allowed("admin_settings"):
            st.button(
                "",
                key="open_alerts",
                icon=":material/notifications:",
                help="Alerts: daily checks that email when a condition is met",
                on_click=open_alerts,
                use_container_width=True,
            )
    with admin_column:
        if allowed("admin_settings"):
            st.button(
                "",
                key="open_admin",
                icon=":material/settings:",
                help="Admin settings: models, checks, Power BI, interface",
                on_click=open_admin,
                use_container_width=True,
            )
    with users_column:
        if allowed("manage_users"):
            st.button(
                "",
                key="open_access",
                icon=":material/manage_accounts:",
                help="Users and folder access: accounts, permissions, document folders",
                on_click=open_access,
                use_container_width=True,
            )
    with account_column:
        auth.account_menu(CURRENT_USER, "acct_chat")

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

    if VOICE_ENABLED:
        st.html(
            '<div class="ip-voice-hint">To ask by voice: click the microphone, '
            "speak, then click the <b>tick (&#10003;)</b> to finish. Your words "
            "appear in the box; press Enter to send.</div>"
        )
        if submission is not None:
            # Levels only, never content (see src/voice.py).
            print(f"[VOICE] submission received: audio="
                  f"{len(audio_bytes) if audio_bytes else 0} bytes, "
                  f"typed={len(typed_text)} characters", flush=True)

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

    # The signed-in account: "email this to me" goes there.
    ACTION_SELF = action_card.self_address_for(
        SETTINGS, current_query_account(SETTINGS["powerbi"]["username"]))

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

        last_index = len(st.session_state.messages) - 1
        # The action buttons sit under the latest data answer only: that is
        # the answer an action works on.
        latest_answer = max((i for i, m in enumerate(st.session_state.messages)
                             if m.get("actionable")), default=None)
        for index, message in enumerate(st.session_state.messages):

            if message["role"] == "user":
                with st.chat_message("user", avatar=":material/person:"):
                    st.markdown(message["content"])
            else:
                with st.chat_message("assistant", avatar=":material/explore:"):
                    render_answer(message["content"])
                    draft = st.session_state.action_drafts.get(message.get("action_id"))
                    # A permission removed meanwhile hides the card, so
                    # nothing can be approved without it.
                    if draft and allowed("actions"):
                        action_card.render(draft, SETTINGS, ACTION_SELF)
                    if index == latest_answer and allowed("actions"):
                        action_card.quick_buttons(SETTINGS, ACTION_SELF,
                                                  f"quick_{index}")
                    # Suggested rephrasings of a declined question, as
                    # buttons on the latest answer only.
                    if index == last_index and message.get("suggestions"):
                        for number, suggestion in enumerate(message["suggestions"]):
                            st.button(
                                suggestion,
                                key=f"suggestion_{index}_{number}",
                                icon=":material/subdirectory_arrow_right:",
                                on_click=queue_prompt,
                                args=(suggestion,),
                            )

    if prompt:

        st.session_state.messages.append({
            "role": "user",
            "content": prompt,
        })

        # "Email this to ...", "export net sales by region": an action
        # request, possibly with a data question inside (answered first).
        action_request = action_detect.parse(prompt)
        question = prompt if action_request is None else action_request["question"]
        answered = False

        with chat_container:
            with st.chat_message("user", avatar=":material/person:"):
                st.markdown(prompt)

            with st.chat_message("assistant", avatar=":material/explore:"):
                if question:
                    answered = answer_question(question)
                if action_request is not None:
                    if not allowed("actions"):
                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": "That looks like a request to "
                                       + action_card.service.KIND_LABELS[
                                           action_request["kind"]].lower()
                                       + ". Your account is not allowed to run "
                                       "actions (email, reminders, exports), so "
                                       "nothing was drafted. Ask an administrator."})
                    elif question and not answered:
                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": "There is no data answer above to "
                                       + action_card.service.KIND_LABELS[
                                           action_request["kind"]].lower()
                                       + ", so nothing was drafted."})
                    else:
                        st.session_state.messages.append(action_card.start(
                            action_request, prompt, SETTINGS, ACTION_SELF))

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

    # Republishing the report changes its page IDs, so unless one is fixed in
    # Admin settings the current "AI Canvas" page is looked up (cached).
    report_id = SETTINGS["powerbi"]["report_id"] or report_component.DEFAULT_REPORT_ID
    page_id = SETTINGS["powerbi"]["page_id"] or current_report_page(
        report_id, SETTINGS["powerbi"]["workspace_id"] or None)

    # Chart type of the latest answer's visual: redraws it, no new query.
    choices = chart_choices(st.session_state.visual_built)
    if choices:
        st.segmented_control(
            "Chart type",
            ["Auto"] + choices,
            key="chart_type",
            on_change=change_chart_type,
            help="Auto is the chart chosen for the answer. Pie and donut are "
                 "offered when the answer has one breakdown and one measure.",
        )

    powerbi_embed_component(
        key="main-powerbi-report",
        visual_request=st.session_state.visual_request,
        report_id=report_id,
        page_id=page_id or None,
        login_hint=current_query_account(SETTINGS["powerbi"]["username"]),
    )

st.html(
    f'<div class="ip-foot">{APP_NAME} · '
    + ("questions are sent to a cloud model · "
       if AGENT_MODE != "structured" or app_settings.uses_cloud(SETTINGS)
       else "runs on this computer · ")
    + "queries are read-only</div>"
)
