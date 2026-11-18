"""The Documents page: ask questions answered from a folder of PDFs.

Opened with the Documents button in the main header or ``?view=documents``.
Design: docs/RAG_PLAN.md. Has its own chat history, separate from the
Power BI chat.
"""

import html
import json
from pathlib import Path

import streamlit as st

from src.rag.answer import AnswerError
from src.rag.embed import EmbedError
from src.rag.indexer import IndexBusy
from src.rag.service import DocumentService, FolderError, clean_folder_id

STATUS_LABELS = {
    "indexed": "Indexed",
    "no_text": "No text layer (scanned?)",
    "password": "Password protected",
    "unreadable": "Cannot be read",
    "error": "Error",
}


def _service(config):
    """One service per settings version, kept for the session."""

    stamp = json.dumps(config, sort_keys=True)
    if st.session_state.get("doc_service_stamp") != stamp:
        st.session_state.doc_service = DocumentService(config)
        st.session_state.doc_service_stamp = stamp
    return st.session_state.doc_service


def _installed_models(base_url):
    from src.llm import list_ollama_models

    try:
        return {m.split(":latest")[0] for m in list_ollama_models(base_url)} | \
            set(list_ollama_models(base_url))
    except Exception:
        return None


def _pages(source):
    if source["page_start"] == source["page_end"]:
        return f"p. {source['page_start']}"
    return f"pp. {source['page_start']}-{source['page_end']}"


def _render_answer(message, folder, show_excerpts, index, read_aloud=True,
                   show_downloads=True, read_aloud_rate=1.0):
    if not message.get("found", True):
        st.info(message["content"], icon=":material/search_off:")
        return
    st.markdown(message["content"])
    if read_aloud and not message["content"].startswith("**Error:**"):
        from src.read_aloud import read_aloud_button

        read_aloud_button(message["content"], f"doc-answer-{index}", read_aloud_rate)
    if not message.get("cited", True):
        st.caption(":material/warning: This answer is not linked to a source; "
                   "check it against the documents.")
    sources = message.get("sources") or []
    if not sources:
        return
    st.caption("Sources: " + " · ".join(
        f"[{s['number']}] {s['file']} {_pages(s)}" for s in sources))
    if show_excerpts:
        with st.expander("Show the passages used"):
            for s in sources:
                st.markdown(f"**[{s['number']}] {s['file']}, {_pages(s)}**")
                # Shown as plain text: a passage may contain Markdown
                # characters ("##", "*") that must not be rendered.
                st.html('<div style="font-size:.85rem;color:#667085;'
                        'white-space:pre-wrap;line-height:1.45">'
                        + html.escape(s["text"]) + "</div>")
    if not show_downloads:
        return
    files = sorted({s["file_key"] for s in sources})
    columns = st.columns(min(len(files), 3))
    for number, key in enumerate(files):
        path = Path(folder) / key
        if path.exists():
            with columns[number % len(columns)]:
                st.download_button(
                    f"Open {Path(key).name}", data=path.read_bytes(),
                    file_name=Path(key).name, mime="application/pdf",
                    key=f"doc_dl_{index}_{number}", icon=":material/picture_as_pdf:",
                    use_container_width=True)


# ----------------------------------------------------------------- voice
# Spoken questions, as on the Power BI page: whisper.cpp on this computer,
# the words go into the box to check (or are sent at once when Admin ->
# Voice -> auto-send is on).

DOC_VOICE_PROMPT = "Questions about the documents in this folder."


@st.cache_resource(show_spinner=False)
def _transcriber():
    from src import voice

    # A neutral prompt: the Power BI page's prompt is sales vocabulary.
    return voice.Transcriber(prompt=DOC_VOICE_PROMPT)


def _question_input(folder_id, folder_ready, history):
    """The question box (with a microphone when speech-to-text is installed).
    Returns the question to answer now, or ``None``."""

    from src import voice

    enabled = voice.is_available()
    notice = st.session_state.pop("doc_voice_notice", None)
    if notice:
        (st.warning if notice["kind"] == "error" else st.info)(
            notice["text"], icon=":material/mic_off:" if notice["kind"] == "error"
            else ":material/mic:")
    # A heard question waiting for review goes into the box; this must be
    # set before the widget is created in this run.
    draft = st.session_state.pop("doc_voice_draft", None)
    if draft:
        st.session_state["doc_chat_input"] = draft

    placeholder = (f"Ask a question about folder {folder_id}"
                   + (", or use the microphone..." if enabled else "...")
                   if folder_ready else "Choose a folder first")
    if enabled:
        submission = st.chat_input(placeholder, key="doc_chat_input",
                                   disabled=not folder_ready, accept_audio=True,
                                   audio_sample_rate=voice.SAMPLE_RATE)
    else:
        submission = st.chat_input(placeholder, key="doc_chat_input",
                                   disabled=not folder_ready)
    typed, audio = voice.split_submission(submission)
    if enabled and folder_ready:
        st.html('<div class="ip-voice-hint">To ask by voice: click the microphone, '
                "speak, then click the <b>tick (&#10003;)</b> to finish. Your words "
                "appear in the box; press Enter to send.</div>")
        if submission is not None:
            # Levels only, never content (see src/voice.py).
            print(f"[VOICE] documents submission: audio="
                  f"{len(audio) if audio else 0} bytes, typed={len(typed)} characters",
                  flush=True)
    if not audio:
        return typed or None

    heard = ""
    with history, st.spinner("Transcribing your voice..."):
        try:
            heard = voice.transcribe_wav(audio, _transcriber())
        except voice.VoiceError as exc:
            st.session_state.doc_voice_notice = {"kind": "error", "text": str(exc)}
    combined = " ".join(part for part in (typed, heard) if part)
    if heard and voice.autosend_enabled():
        return combined
    if heard:
        st.session_state.doc_voice_notice = {
            "kind": "info", "text": "Check the question in the box, then press Enter "
                                    "to send it."}
    st.session_state.doc_voice_draft = combined or None
    st.rerun()


def _run_index(service, rebuild, slot):
    bar = slot.progress(0.0, text="Starting...")

    def progress(message, done, total):
        bar.progress(min(done / total, 1.0) if total else 1.0, text=message)

    try:
        summary = service.index(rebuild=rebuild, progress=progress)
    except IndexBusy as exc:
        slot.warning(str(exc), icon=":material/hourglass_top:")
        return
    except EmbedError as exc:
        slot.error(str(exc), icon=":material/error:")
        return
    parts = [f"{summary[k]} {k}" for k in ("added", "updated", "removed",
                                            "skipped", "failed") if summary[k]]
    st.session_state.doc_notice = ("Index up to date"
                                   + (": " + ", ".join(parts) if parts else "."))
    st.rerun()


def render(settings, user):
    """``user``: the signed-in user (``src.security``); only the folders
    assigned to them can be chosen and searched."""

    from src.security import login as auth
    from src.security import users as auth_users
    from src.settings import DEFAULTS

    config = {**DEFAULTS["documents"], **settings["documents"]}
    service = _service(config)
    folder = service.folder
    folder.mkdir(parents=True, exist_ok=True)
    histories = st.session_state.setdefault("doc_histories", {})

    on_disk = [p.name for p in folder.iterdir()
               if p.is_dir() and auth_users.FOLDER_ID.fullmatch(p.name)]
    choices = auth_users.allowed_folders(user, on_disk)

    # The folder can come from the link (?view=documents&folder=102), only
    # if this user may open it.
    if "doc_folder_input" not in st.session_state:
        linked = st.query_params.get("folder", "")
        st.session_state.doc_folder_input = (
            linked if linked in choices else (choices[0] if len(choices) == 1 else None))
    if st.session_state.doc_folder_input not in choices:
        st.session_state.doc_folder_input = None

    # ------------------------------------------------------------ header
    title, folder_box, new_chat, back = st.columns(
        [3.2, 1.3, 1, 1.3], vertical_alignment="bottom")
    with title:
        st.html('<div class="ip-card-title" style="font-size:1.25rem">Ask your '
                'documents</div><div class="ip-card-sub">Each folder is separate: '
                'answers come only from the PDFs of the folder you choose, '
                'with the file and page of every source.</div>')
    with folder_box:
        picked = st.selectbox("Folder", choices, key="doc_folder_input",
                              placeholder="Choose a folder" if choices
                              else "No folders assigned",
                              disabled=not choices,
                              help="Only the folders an administrator assigned to "
                                   "you. Only the PDFs in documents\\<folder> are "
                                   "searched.")

    folder_id, folder_error = None, None
    try:
        folder_id = clean_folder_id(picked)
    except FolderError as exc:
        folder_error = str(exc)
    # Checked again here, not only by the list: the answer must never come
    # from a folder this user may not open.
    if folder_id and not auth_users.folder_allowed(user, folder_id):
        folder_id, folder_error = None, "You do not have access to that folder."
    if folder_id and st.query_params.get("folder") != folder_id:
        st.query_params["folder"] = folder_id
    messages = histories.setdefault(folder_id or "", [])

    with new_chat:
        if st.button("New chat", key="doc_new_chat", icon=":material/add_comment:",
                     disabled=not messages, use_container_width=True):
            histories[folder_id or ""] = []
            st.rerun()
    with back:
        if auth_users.can(user, "power_bi"):
            if st.button("Back to Power BI", key="doc_back", icon=":material/arrow_back:",
                         use_container_width=True):
                st.session_state.view = "chat"
                st.query_params.clear()
                st.rerun()
        else:
            auth.account_menu(user, "acct_docs")

    installed = _installed_models(config["ollama_url"])
    missing = [] if installed is None else [
        m for m in (config["embedding_model"], config["answer_model"])
        if m not in installed and f"{m}:latest" not in installed]
    if installed is None:
        st.warning("Ollama is not responding, so documents cannot be indexed or "
                   "searched. Start Ollama and reload.", icon=":material/cloud_off:")
    elif missing:
        st.warning("Model not installed yet: " + ", ".join(missing) + ". Run `ollama "
                   "pull " + missing[0] + "` (or wait for a download in progress).",
                   icon=":material/download:")

    chat_column, library_column = st.columns([5, 3], gap="medium")

    # ----------------------------------------------------------- library
    with library_column, st.container(border=True, key="doc_library"):
        st.html('<div class="ip-card-title">Library'
                + (f" · folder {folder_id}" if folder_id else "") + '</div>')
        st.caption(f"Documents folder: `{folder}`. Put each group of PDFs in its "
                   "own subfolder, for example `documents\\102`.")
        notice = st.session_state.pop("doc_notice", None)
        if notice:
            st.success(notice, icon=":material/check_circle:")

        # Indexing covers every folder; only counts are shown here.
        files, work = service.pending()
        changes = len(work["new"]) + len(work["changed"]) + len(work["deleted"])
        progress_slot = st.empty()
        can_index = installed is not None and config["embedding_model"] not in missing
        if service.needs_rebuild():
            st.warning("The search settings changed in Admin (model, passage "
                       "size or text extraction): every PDF is indexed again.",
                       icon=":material/autorenew:")
        elif changes:
            st.caption(", ".join(f"{len(work[k])} {k}" for k in
                                 ("new", "changed", "deleted") if work[k])
                       + " file(s) since the last indexing.")
        index_now = st.button("Index now" if changes else "Index is up to date",
                              key="doc_index", type="primary" if changes else "secondary",
                              disabled=not (changes and can_index),
                              icon=":material/sync:", use_container_width=True)
        # New or changed files are indexed automatically once per session.
        auto = (changes and can_index and config["auto_index"]
                and not st.session_state.get("doc_auto_done"))
        if index_now or auto:
            st.session_state.doc_auto_done = True
            _run_index(service, False, progress_slot)

        loose = service.unfoldered()
        if loose:
            st.warning(f"{len(loose)} PDF(s) are directly in the documents folder, "
                       "not in a numbered subfolder, so they are never searched.",
                       icon=":material/folder_off:")

        folder_ready = False
        if folder_error:
            st.error(folder_error, icon=":material/error:")
        elif not folder_id:
            st.info("Choose a folder above to see and search its documents."
                    if choices else "No document folders are assigned to your "
                    "account. Ask an administrator (Users and folder access).",
                    icon=":material/folder:")
        else:
            library = service.library(folder_id)
            indexed = [k for k, e in sorted(library.items()) if e.get("status") == "indexed"]
            folder_ready = bool(indexed)
            if not library:
                st.info(f"There are no documents in folder {folder_id}.",
                        icon=":material/folder_off:")
            else:
                pages = sum(e.get("pages", 0) for e in library.values()
                            if e.get("status") == "indexed")
                chunks = sum(e.get("chunks", 0) for e in library.values())
                a, b, c = st.columns(3)
                a.metric("PDFs", len(library))
                b.metric("Pages", f"{pages:,}")
                c.metric("Passages", f"{chunks:,}")
                rows = [{"File": key.split("/", 1)[1],
                         "Status": STATUS_LABELS.get(entry.get("status"), entry.get("status")),
                         "Pages": entry.get("pages", 0),
                         "Passages": entry.get("chunks", 0)}
                        for key, entry in sorted(library.items())]
                st.dataframe(rows, hide_index=True, use_container_width=True,
                             height=min(38 + 35 * len(rows), 320))
                if len(indexed) > 1:
                    st.multiselect("Search only in", indexed, key=f"doc_filter_{folder_id}",
                                   format_func=lambda k: k.split("/", 1)[1],
                                   placeholder="All documents in this folder")
        with st.expander("Rebuild the whole index"):
            st.caption("Reads every PDF in every folder again. Changing the "
                       "search settings in Admin triggers this by itself.")
            if st.button("Rebuild", key="doc_rebuild", disabled=not can_index):
                _run_index(service, True, progress_slot)

    # -------------------------------------------------------------- chat
    with chat_column, st.container(border=True, key="doc_chat_card"):
        history = st.container(height=settings["interface"]["chat_height"] - 60,
                               border=False, key="doc_chat_scroll")
        with history:
            if not messages:
                if folder_ready:
                    st.html(f'<div class="ip-welcome"><h3>Ask about folder '
                            f'{html.escape(folder_id)}</h3>'
                            + ('<p>Try one of these:</p>' if config["example_questions"]
                               else '') + '</div>')
                    for number, example in enumerate(config["example_questions"]):
                        if st.button(example, key=f"doc_example_{number}",
                                     use_container_width=True):
                            st.session_state.doc_queued = example
                            st.rerun()
                else:
                    st.html('<div class="ip-welcome"><h3>Choose a folder</h3>'
                            '<p>Questions are answered only from the PDFs of the '
                            'folder you choose (for example <b>102</b> for '
                            '<code>documents\\102</code>). You see only the folders '
                            'assigned to you.</p></div>')
            for index, message in enumerate(messages):
                if message["role"] == "user":
                    with st.chat_message("user", avatar=":material/person:"):
                        st.markdown(message["content"])
                else:
                    with st.chat_message("assistant", avatar=":material/description:"):
                        _render_answer(message, folder, config["show_excerpts"],
                                       f"{folder_id}-{index}", config["read_aloud"],
                                       config["show_downloads"], config["read_aloud_rate"])

        question = _question_input(folder_id, folder_ready, history)
        queued = st.session_state.pop("doc_queued", None)
        if queued and not question:
            question = queued

        if question and folder_ready:
            messages.append({"role": "user", "content": question})
            with history:
                with st.chat_message("user", avatar=":material/person:"):
                    st.markdown(question)
                with st.chat_message("assistant", avatar=":material/description:"):
                    with st.spinner(f"Searching folder {folder_id} and writing the answer..."):
                        turns = [{"role": m["role"], "content": m["content"]}
                                 for m in messages[:-1]]
                        try:
                            result = service.ask(
                                question, turns, folder_id,
                                st.session_state.get(f"doc_filter_{folder_id}") or None)
                            message = {"role": "assistant", "content": result["text"],
                                       "sources": result["sources"],
                                       "found": result["found"],
                                       "cited": result["cited"]}
                        except FolderError as exc:
                            message = {"role": "assistant", "content": str(exc),
                                       "found": False}
                        except (AnswerError, EmbedError) as exc:
                            message = {"role": "assistant",
                                       "content": f"**Error:** {exc}", "found": True}
            messages.append(message)
            st.rerun()
