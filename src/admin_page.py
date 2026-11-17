"""The Admin page: every tunable setting of the app in one place.

Rendered by ``src/main.py`` when the gear button is used (or the address ends
in ``?view=admin``). Values are read from and written to ``src/settings.py``.
"""

import os

import streamlit as st

from src import llm
from src import settings as app_settings

PREFIX = "adm_"

PROVIDER_HINTS = {
    "ollama": (
        "Runs on this computer. Free; questions never leave the PC. Install "
        "more models with `ollama pull <name>`."
    ),
    "openai_compatible": (
        "Any service with an OpenAI-style `/chat/completions` endpoint. "
        "Examples of the address: OpenAI `https://api.openai.com/v1`, "
        "OpenRouter `https://openrouter.ai/api/v1`, Groq "
        "`https://api.groq.com/openai/v1`, Google Gemini "
        "`https://generativelanguage.googleapis.com/v1beta/openai`, LM Studio "
        "on this PC `http://localhost:1234/v1`. Billed by the provider per use."
    ),
    "anthropic": (
        "Claude models through Anthropic's API, for example `claude-opus-5-5` "
        "(most capable Opus), `claude-sonnet-5-5` or `claude-haiku-5-5` "
        "(cheapest). Billed by Anthropic per use."
    ),
}


def _key(name):
    return PREFIX + name


def _provider_changed(prefix):
    """Fill in sensible address/model defaults when the provider changes."""

    provider = st.session_state[_key(f"{prefix}_provider")]
    st.session_state[_key(f"{prefix}_base_url")] = app_settings.DEFAULT_BASE_URLS[provider]
    st.session_state[_key(f"{prefix}_model")] = app_settings.DEFAULT_MODELS[provider]


def _clear_widget_state():
    for name in [k for k in st.session_state if k.startswith(PREFIX)]:
        del st.session_state[name]


def _model_editor(prefix, block, secrets, saved_key_name):
    """Widgets for one model block. Returns ``(block, new_key, remove_key)``."""

    provider = st.selectbox(
        "Provider",
        app_settings.PROVIDERS,
        index=app_settings.PROVIDERS.index(block["provider"]),
        format_func=app_settings.PROVIDER_LABELS.get,
        key=_key(f"{prefix}_provider"),
        on_change=_provider_changed,
        args=(prefix,),
    )
    st.caption(PROVIDER_HINTS[provider])

    left, right = st.columns(2)
    with left:
        model = st.text_input(
            "Model name", value=block["model"], key=_key(f"{prefix}_model"),
            help="Exactly as the provider names it.")
    with right:
        base_url = st.text_input(
            "Service address",
            value=block["base_url"],
            key=_key(f"{prefix}_base_url"),
            placeholder="Leave empty for Anthropic's own API"
            if provider == "anthropic" else "",
            help="Where requests are sent. Change it only for a proxy or a "
                 "different server.",
        )

    if provider == "ollama":
        installed = llm.list_ollama_models(
            base_url or app_settings.DEFAULT_BASE_URLS["ollama"])
        if installed:
            st.caption("Installed in Ollama: " + ", ".join(f"`{m}`" for m in installed))
            if model and model not in installed:
                st.warning(
                    f"`{model}` is not installed in Ollama. Run "
                    f"`ollama pull {model}` first.", icon=":material/warning:")
        else:
            st.caption("Ollama is not reachable at this address right now.")

    new_key, remove_key = "", False
    if provider != "ollama":
        saved = secrets.get(saved_key_name, "")
        key_left, key_right = st.columns([3, 1], vertical_alignment="bottom")
        with key_left:
            new_key = st.text_input(
                "API key",
                type="password",
                key=_key(f"{prefix}_api_key"),
                placeholder=(f"Saved: {app_settings.mask(saved)} (leave empty to keep)"
                             if saved else "Paste the key from the provider"),
                help="Stored encrypted for your Windows account in "
                     "config\\secrets.bin. Never written to settings.json or "
                     "the logs.",
            )
        with key_right:
            remove_key = st.checkbox(
                "Remove saved key", key=_key(f"{prefix}_remove_key"),
                disabled=not saved)
        if provider == "anthropic" and not saved:
            st.caption(
                "Without a saved key, the `ANTHROPIC_API_KEY` environment "
                "variable or an `ant auth login` profile is used if present.")

    with st.expander("Tuning", expanded=False):
        a, b, c = st.columns(3)
        with a:
            timeout = st.number_input(
                "Wait for an answer (seconds)", min_value=5, max_value=3600,
                value=int(block["timeout_seconds"]), step=5,
                key=_key(f"{prefix}_timeout"),
                help="How long one model call may take before it is abandoned.")
        with b:
            max_output = st.number_input(
                "Maximum output tokens", min_value=16, max_value=128000,
                value=block["max_output_tokens"], step=256,
                key=_key(f"{prefix}_max_output"), placeholder="Provider default",
                help="Empty = the provider's default. A query plan needs "
                     "roughly 300 tokens; models that think first need far more.")
        temperature, top_p, seed = block["temperature"], block["top_p"], block["seed"]
        num_ctx, keep_alive = block["num_ctx"], block["keep_alive"]
        json_mode, token_parameter = block["json_mode"], block["token_parameter"]
        reasoning_effort, effort = block["reasoning_effort"], block["effort"]
        refusal_fallback = block["refusal_fallback"]

        if provider in ("ollama", "openai_compatible"):
            with c:
                temperature = st.number_input(
                    "Temperature", min_value=0.0, max_value=2.0,
                    value=block["temperature"], step=0.1,
                    key=_key(f"{prefix}_temperature"), placeholder="Provider default",
                    help="0 = the same answer every time (recommended for "
                         "queries). Empty = do not send; needed for some "
                         "reasoning models that reject this setting.")
            d, e, f = st.columns(3)
            with d:
                top_p = st.number_input(
                    "Top-p", min_value=0.0, max_value=1.0, value=block["top_p"],
                    step=0.05, key=_key(f"{prefix}_top_p"),
                    placeholder="Provider default",
                    help="Alternative to temperature. Leave empty normally.")
            with e:
                seed = st.number_input(
                    "Seed", min_value=0, max_value=2**31 - 1, value=block["seed"],
                    step=1, key=_key(f"{prefix}_seed"), placeholder="Not set",
                    help="Fixes randomness when temperature is above 0.")

        if provider == "ollama":
            g, h, _ = st.columns(3)
            with g:
                num_ctx = st.number_input(
                    "Context window (tokens)", min_value=1024, max_value=1048576,
                    value=int(block["num_ctx"]), step=1024,
                    key=_key(f"{prefix}_num_ctx"),
                    help="Must hold the instructions (about 5,000 tokens) plus "
                         "the question. Larger uses more memory.")
            with h:
                keep_alive = st.text_input(
                    "Keep model loaded", value=block["keep_alive"],
                    key=_key(f"{prefix}_keep_alive"), placeholder="Ollama default (5m)",
                    help="For example 30m, 2h, or -1 to keep it loaded always. "
                         "Longer avoids a slow first answer after a pause.")

        if provider == "openai_compatible":
            i, j, k = st.columns(3)
            with i:
                json_mode = st.checkbox(
                    "Ask for JSON output", value=block["json_mode"],
                    key=_key(f"{prefix}_json_mode"),
                    help="Sends response_format = json_object. Turn off only "
                         "if the service rejects it.")
            with j:
                token_parameter = st.selectbox(
                    "Name of the token-limit setting",
                    app_settings.TOKEN_PARAMETERS,
                    index=app_settings.TOKEN_PARAMETERS.index(block["token_parameter"]),
                    key=_key(f"{prefix}_token_parameter"),
                    help="Newer OpenAI models require max_completion_tokens; "
                         "most other services use max_tokens.")
            with k:
                reasoning_effort = st.text_input(
                    "Reasoning effort", value=block["reasoning_effort"],
                    key=_key(f"{prefix}_reasoning_effort"), placeholder="Not sent",
                    help="For reasoning models only, e.g. low, medium, high.")

        if provider == "anthropic":
            l, m, _ = st.columns(3)
            with l:
                effort = st.selectbox(
                    "Effort",
                    app_settings.EFFORT_LEVELS,
                    index=app_settings.EFFORT_LEVELS.index(block["effort"]),
                    format_func=lambda v: v or "Model default",
                    key=_key(f"{prefix}_effort"),
                    help="How much Claude thinks before answering. Lower is "
                         "faster and cheaper; `low` suits short query plans. "
                         "Claude models do not accept temperature.")
            with m:
                refusal_fallback = st.checkbox(
                    "Retry on a fallback model if declined",
                    value=block["refusal_fallback"],
                    key=_key(f"{prefix}_refusal_fallback"),
                    help="If a safety filter declines a request, Anthropic "
                         "re-runs it on another Claude model in the same call.")

    edited = dict(
        block, provider=provider, model=model, base_url=base_url,
        timeout_seconds=timeout, max_output_tokens=max_output,
        temperature=temperature, top_p=top_p, seed=seed, num_ctx=num_ctx,
        keep_alive=keep_alive, json_mode=json_mode,
        token_parameter=token_parameter, reasoning_effort=reasoning_effort,
        effort=effort, refusal_fallback=refusal_fallback,
    )
    return edited, new_key, remove_key


def _test_button(label, block, api_key, key):
    if st.button(label, key=_key(key), icon=":material/network_check:"):
        clean, errors = app_settings.validate({"planner": block})
        if errors:
            st.error("\n\n".join(errors))
            return
        with st.spinner("Asking the model..."):
            ok, message = llm.test_connection(clean["planner"], api_key)
        (st.success if ok else st.error)(message)


def render(agent_mode_in_use, voice_installed):
    """Draw the Admin page. Returns True if settings were saved this run."""

    saved = app_settings.load()
    secrets = app_settings.load_secrets()

    top_left, top_right = st.columns([4, 1], vertical_alignment="center")
    with top_left:
        st.html(
            '<div class="ip-card-title" style="font-size:1.25rem">Admin settings</div>'
            '<div class="ip-card-sub">Models, answer checks, Power BI and the '
            "interface. Changes apply to the next question after you save.</div>"
        )
    with top_right:
        if st.button("Back to InsightPilot", key=_key("back"),
                     icon=":material/arrow_back:", use_container_width=True):
            st.session_state.view = "chat"
            st.rerun()

    notice = st.session_state.pop("admin_notice", None)
    if notice:
        st.success(notice, icon=":material/check_circle:")

    tabs = st.tabs(["Models", "Answer checks", "Power BI", "Interface",
                    "Voice", "Documents", "About"])

    # ---------------------------------------------------------- Models
    with tabs[0]:
        mode_labels = {
            "launcher": f"Decided by the launcher (now: {agent_mode_in_use})",
            "structured": "Structured pipeline: plans a query, checks it, runs it read-only",
            "foundry": "Azure AI Foundry agent (cloud, billed by Azure; not covered by the measured results)",
        }
        agent_mode = st.selectbox(
            "Assistant",
            app_settings.AGENT_MODES,
            index=app_settings.AGENT_MODES.index(saved["agent"]["mode"]),
            format_func=mode_labels.get,
            key=_key("agent_mode"),
            help="The background server starts the structured pipeline. The "
                 "settings below apply to the structured pipeline.",
        )

        st.subheader("Planner model", divider="gray")
        st.caption("Turns the question into a query plan. This is the model "
                   "that decides answer quality.")
        planner, planner_key, planner_remove = _model_editor(
            "planner", saved["planner"], secrets, "planner_api_key")
        _test_button(
            "Test planner model", planner,
            planner_key or ("" if planner_remove else secrets.get("planner_api_key", "")),
            "test_planner")

        st.subheader("Verifier model", divider="gray")
        st.caption("Double-checks that a plan really answers the question "
                   "(see Answer checks). A stronger verifier than planner "
                   "catches more wrong answers.")
        same = st.checkbox(
            "Use the planner model for verifying",
            value=saved["verifier"]["same_as_planner"], key=_key("verifier_same"))
        verifier, verifier_key, verifier_remove = dict(saved["verifier"]), "", False
        if not same:
            block = {k: v for k, v in saved["verifier"].items() if k != "same_as_planner"}
            verifier, verifier_key, verifier_remove = _model_editor(
                "verifier", block, secrets, "verifier_api_key")
            _test_button(
                "Test verifier model", verifier,
                verifier_key or ("" if verifier_remove else secrets.get("verifier_api_key", "")),
                "test_verifier")
        verifier["same_as_planner"] = same

        with st.expander("Azure AI Foundry agent settings"):
            st.caption("Used only when the assistant above is the Foundry agent.")
            foundry_endpoint = st.text_input(
                "Project endpoint", value=saved["foundry"]["endpoint"],
                key=_key("foundry_endpoint"), placeholder="Built-in endpoint")
            foundry_model = st.text_input(
                "Model deployment name", value=saved["foundry"]["model"],
                key=_key("foundry_model"), placeholder="Built-in (gpt-5.2)")

    # --------------------------------------------------- Answer checks
    with tabs[1]:
        st.caption(
            "These protect against wrong or unsupported answers. The defaults "
            "are the configuration that was measured (pipeline v11).")
        p = saved["pipeline"]
        request_guard = st.checkbox(
            "Refuse requests to change data", value=p["request_guard"],
            key=_key("request_guard"),
            help='Blocks questions like "Change the Q4 target" before any '
                 "model is called. Queries are read-only regardless.")
        repair_turn = st.checkbox(
            "Let the model fix a rejected plan once", value=p["repair_turn"],
            key=_key("repair_turn"),
            help="When a plan fails validation, the model gets the reason and "
                 "one more try. Off = fewer model calls, more failed questions.")
        answerability = st.checkbox(
            "Verify that the plan answers the question",
            value=p["answerability_check"], key=_key("answerability_check"),
            help="Runs the verifier when the question uses words that are not "
                 "in the data model. Off = unsupported questions such as "
                 "forecasts may be answered with unrelated data.")
        no_metric = st.checkbox(
            "Also verify questions that name no metric",
            value=p["no_metric_trigger"], key=_key("no_metric_trigger"),
            disabled=not answerability,
            help='Catches vague questions such as "Show me the totals".')
        strict_words = st.checkbox(
            "Stricter: a grouping word alone is not a metric (experimental)",
            value=p["strict_metric_words"], key=_key("strict_metric_words"),
            disabled=not answerability,
            help='Catches "Rank the regions". In testing (pipeline v12) it '
                 "declined 6 of 8 vague questions instead of 0, but also "
                 'wrongly declined "Distinct stores per channel", so it is '
                 "off by default.")
        fail_closed = st.checkbox(
            "Do not answer when the verifier's reply is unusable",
            value=p["verifier_fail_closed"], key=_key("verifier_fail_closed"),
            disabled=not answerability,
            help="By default an unreadable verifier reply lets the answer "
                 "through. On = safer, but an occasional good question is "
                 "declined.")
        measure_names = st.checkbox(
            'Decline measures the model does not have ("net profit")',
            value=p["measure_name_check"], key=_key("measure_name_check"),
            help='A "net" or "gross" phrase that names no measure is '
                 "declined with the closest measures, instead of being "
                 "answered with a different measure. Added after the "
                 "measured pipeline v11; not part of its accuracy figures.")
        interpret_wording = st.checkbox(
            "Understand everyday wording, relative dates and typos",
            value=p["interpret_wording"], key=_key("interpret_wording"),
            help='"branch" is read as store, "revenue" as net sales, "this '
                 'year" as the current year, "regoin" as region, and so on; '
                 "the answer says how the question was read. A declined "
                 "question comes with a suggested rephrasing. Added after "
                 "the measured pipeline v11; not part of its accuracy "
                 "figures.")
        max_rows = st.number_input(
            "Rows shown per answer", min_value=1, max_value=5000,
            value=int(p["max_table_rows"]), step=10, key=_key("max_table_rows"))

    # -------------------------------------------------------- Power BI
    with tabs[2]:
        st.caption(
            "Leave a field empty to use the built-in value or the matching "
            "environment variable.")
        b = saved["powerbi"]
        st.warning(
            "The planner knows the tables, columns and values of the current "
            "dataset from a saved snapshot (`evaluation/semantic_model_schema"
            ".json`). Pointing at a different dataset without refreshing that "
            "snapshot will produce failing queries.",
            icon=":material/warning:")
        one, two = st.columns(2)
        with one:
            dataset_id = st.text_input(
                "Dataset ID (questions)", value=b["dataset_id"],
                key=_key("dataset_id"),
                placeholder=os.environ.get("POWERBI_DATASET_ID", "Built-in dataset"))
            workspace_id = st.text_input(
                "Workspace ID", value=b["workspace_id"], key=_key("workspace_id"),
                placeholder=os.environ.get("POWERBI_WORKSPACE_ID", "My workspace"))
            username = st.text_input(
                "Sign-in account", value=b["username"], key=_key("username"),
                placeholder=os.environ.get("ENTRA_USERNAME", "First signed-in account"),
                help="The Microsoft account that has Power BI. Answers use "
                     "it, and the report panel's sign-in suggests it in the "
                     "account chooser. Leave empty to use the first saved "
                     "sign-in.")
        with two:
            report_id = st.text_input(
                "Report ID (right-hand panel)", value=b["report_id"],
                key=_key("report_id"), placeholder="Built-in report")
            page_id = st.text_input(
                "Report page ID", value=b["page_id"], key=_key("page_id"),
                placeholder="Automatic: the page named AI Canvas",
                help="Leave empty. The page is then looked up by name each "
                     "time, so republishing the report from Power BI Desktop "
                     "cannot break the link. Charts are added to this page.")
            powerbi_timeout = st.number_input(
                "Wait for Power BI (seconds)", min_value=5, max_value=3600,
                value=int(b["timeout_seconds"]), step=5, key=_key("powerbi_timeout"))
        st.caption(
            "Tenant and application IDs come from the `ENTRA_TENANT_ID` and "
            "`ENTRA_CLIENT_ID` environment variables: "
            + ("both are set." if os.environ.get("ENTRA_TENANT_ID")
               and os.environ.get("ENTRA_CLIENT_ID") else "**not set**."))

    # ------------------------------------------------------- Interface
    with tabs[3]:
        i = saved["interface"]
        starters = st.text_area(
            "Starter questions (one per line, up to 8)",
            value="\n".join(i["starter_questions"]), height=150,
            key=_key("starter_questions"),
            help="Shown as buttons on the empty chat screen.")
        col_a, col_b = st.columns(2)
        with col_a:
            chat_height = st.number_input(
                "Chat panel height (pixels)", min_value=300, max_value=2000,
                value=int(i["chat_height"]), step=20, key=_key("chat_height"))
        with col_b:
            show_details = st.checkbox(
                'Show "How this was answered" under each answer',
                value=i["show_query_details"], key=_key("show_query_details"),
                help="The query plan and the DAX that was run.")
        create_charts = st.checkbox(
            "Add a chart of each answer to the Power BI report",
            value=i["create_charts"], key=_key("create_charts"),
            help="Built from the checked query plan, no model involved. A "
                 "chart is added when the answer has one breakdown, uses "
                 "model measures and is not filtered or limited. You must be "
                 "signed in to the report on the right to see it.")

    # ----------------------------------------------------------- Voice
    with tabs[4]:
        v = saved["voice"]
        if not voice_installed:
            st.info(
                "Voice input is not installed yet (see docs/VOICE_INPUT_PLAN"
                ".md). These settings take effect once it is.",
                icon=":material/mic_off:")
        voice_autosend = st.checkbox(
            "Send spoken questions immediately, without review",
            value=v["autosend"], key=_key("voice_autosend"),
            help="Off = the heard text is placed in the box for you to check.")
        voice_model = st.text_input(
            "Speech model", value=v["model"], key=_key("voice_model"),
            help="base.en (141 MB, about 1 second per question) or small.en "
                 "(488 MB, about 3 seconds). Needs a restart of InsightPilot.")

    # ------------------------------------------------------- Documents
    d = saved["documents"]
    with tabs[5]:
        st.caption("The Documents page answers questions from the PDFs in a "
                   "folder, using local Ollama models. PDF text never leaves "
                   "this computer.")
        one, two = st.columns(2)
        with one:
            doc_folder = st.text_input(
                "PDF folder", value=d["folder"], key=_key("doc_folder"),
                help="An absolute path, or a folder inside the project. The "
                     "default 'documents' is created in the project folder.")
            doc_recursive = st.checkbox("Include subfolders", value=d["recursive"],
                                        key=_key("doc_recursive"))
            doc_embedding = st.text_input(
                "Embedding model (Ollama)", value=d["embedding_model"],
                key=_key("doc_embedding"),
                help="Turns passages into vectors for search. Changing it needs "
                     "Rebuild on the Documents page. English: nomic-embed-text; "
                     "multilingual: bge-m3.")
            doc_answer = st.text_input(
                "Answer model (Ollama)", value=d["answer_model"],
                key=_key("doc_answer"),
                help="Writes the answer from the retrieved passages.")
            doc_url = st.text_input("Ollama address", value=d["ollama_url"],
                                    key=_key("doc_url"))
        with two:
            doc_top_k = st.number_input(
                "Passages per answer", 1, 20, int(d["top_k"]), key=_key("doc_top_k"))
            doc_min_score = st.number_input(
                "Minimum relevance", 0.0, 1.0, float(d["min_score"]), step=0.05,
                key=_key("doc_min_score"),
                help="Below this, the page answers that the documents do not "
                     "cover the question, without asking the model.")
            doc_chunk = st.number_input(
                "Passage size (characters)", 300, 5000, int(d["chunk_chars"]),
                step=100, key=_key("doc_chunk"),
                help="Changing it needs Rebuild on the Documents page.")
            doc_overlap = st.number_input(
                "Passage overlap (characters)", 0, 1000, int(d["overlap_chars"]),
                step=50, key=_key("doc_overlap"))
            doc_timeout = st.number_input(
                "Answer timeout (seconds)", 10, 3600, int(d["answer_timeout"]),
                key=_key("doc_timeout"))
            doc_excerpts = st.checkbox(
                "Show the passages used under each answer",
                value=d["show_excerpts"], key=_key("doc_excerpts"))
            doc_read_aloud = st.checkbox(
                "Read aloud button under each answer",
                value=d["read_aloud"], key=_key("doc_read_aloud"),
                help="Uses the browser's own voices (local Windows voices "
                     "first), so the answer is not sent anywhere.")

    # ----------------------------------------------------------- About
    with tabs[6]:
        st.markdown(
            f"""
* Settings file: `{app_settings.SETTINGS_FILE}`
* API keys: `{app_settings.SECRETS_FILE}` (encrypted for your Windows account)
* Measured accuracy is documented in `docs/EVALUATION.md` and applies **only**
  to the default settings: local `qwen2.5-coder:7b`, all checks as shipped.
  A different model may be better or worse; it has not been measured.
* With a cloud model, each question, the planner instructions (table, column
  and measure names, sample column values) and the draft query plan are sent
  to that provider. Query results are never sent to any model.
* Provider subscriptions for chat apps (for example ChatGPT Plus or Claude
  Pro) do not include API access. You need an API key from the provider's
  developer console, billed per use.
"""
        )

    # ---------------------------------------------------------- Assemble
    edited = {
        "agent": {"mode": agent_mode},
        "planner": planner,
        "verifier": verifier,
        "pipeline": {
            "request_guard": request_guard,
            "repair_turn": repair_turn,
            "answerability_check": answerability,
            "no_metric_trigger": no_metric,
            "strict_metric_words": strict_words,
            "verifier_fail_closed": fail_closed,
            "measure_name_check": measure_names,
            "interpret_wording": interpret_wording,
            "max_table_rows": max_rows,
        },
        "powerbi": {
            "dataset_id": dataset_id, "workspace_id": workspace_id,
            "username": username, "report_id": report_id, "page_id": page_id,
            "timeout_seconds": powerbi_timeout,
        },
        "foundry": {"endpoint": foundry_endpoint, "model": foundry_model},
        "interface": {
            "starter_questions": starters.splitlines(),
            "chat_height": chat_height,
            "show_query_details": show_details,
            "create_charts": create_charts,
        },
        "voice": {"autosend": voice_autosend, "model": voice_model},
        "documents": {
            "folder": doc_folder, "recursive": doc_recursive,
            "ollama_url": doc_url, "embedding_model": doc_embedding,
            "answer_model": doc_answer, "answer_timeout": doc_timeout,
            "chunk_chars": doc_chunk, "overlap_chars": doc_overlap,
            "top_k": doc_top_k, "min_score": doc_min_score,
            "show_excerpts": doc_excerpts,
            "read_aloud": doc_read_aloud,
        },
    }
    clean, errors = app_settings.validate(edited)
    key_changes = bool(planner_key or planner_remove or verifier_key or verifier_remove)
    changed = clean != saved or key_changes

    st.divider()
    if app_settings.uses_cloud(clean):
        st.warning(
            "A cloud model is selected: questions and the data model's names "
            "will be sent to that provider, and each question is billed.",
            icon=":material/cloud:")
    if not app_settings.is_default(clean):
        st.info(
            "These settings differ from the measured defaults, so the "
            "accuracy figures in docs/EVALUATION.md no longer describe the app.",
            icon=":material/science:")
    for error in errors:
        st.error(error, icon=":material/error:")

    save_col, discard_col, reset_col, _ = st.columns([1, 1, 1.3, 2])
    did_save = False
    with save_col:
        if st.button("Save settings", key=_key("save"), type="primary",
                     disabled=bool(errors) or not changed,
                     icon=":material/save:", use_container_width=True):
            app_settings.save(clean)
            if planner_remove:
                app_settings.save_secret("planner_api_key", "")
            elif planner_key:
                app_settings.save_secret("planner_api_key", planner_key)
            if verifier_remove:
                app_settings.save_secret("verifier_api_key", "")
            elif verifier_key:
                app_settings.save_secret("verifier_api_key", verifier_key)
            _clear_widget_state()
            st.session_state.admin_notice = (
                "Settings saved. They apply to the next question.")
            did_save = True
    with discard_col:
        if st.button("Discard changes", key=_key("discard"), disabled=not changed,
                     use_container_width=True):
            _clear_widget_state()
            st.rerun()
    with reset_col:
        if st.button("Reset all to defaults", key=_key("reset"),
                     disabled=app_settings.is_default(saved),
                     icon=":material/restart_alt:", use_container_width=True):
            app_settings.reset()
            _clear_widget_state()
            st.session_state.admin_notice = (
                "All settings are back to the measured defaults. Saved API "
                "keys were kept.")
            did_save = True
    if changed and not errors:
        st.caption("You have unsaved changes.")
    return did_save
