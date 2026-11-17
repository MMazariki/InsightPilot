"""The Documents page: ask questions answered from a folder of PDFs.

Opened with the Documents button in the main header or ``?view=documents``.
Design: docs/RAG_PLAN.md. Has its own chat history, separate from the
Power BI chat.
"""

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


def _render_answer(message, folder, show_excerpts, index, read_aloud=True):
    if not message.get("found", True):
        st.info(message["content"], icon=":material/search_off:")
        return
    st.markdown(message["content"])
    if read_aloud and not message["content"].startswith("**Error:**"):
        from src.read_aloud import read_aloud_button

        read_aloud_button(message["content"], f"doc-answer-{index}")
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
                st.caption(s["text"])
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


def render(settings):
    config = settings["documents"]
    service = _service(config)
    folder = service.folder
    folder.mkdir(parents=True, exist_ok=True)
    histories = st.session_state.setdefault("doc_histories", {})

    # The folder number can come from the link: ?view=documents&folder=102
    if "doc_folder_input" not in st.session_state:
        st.session_state.doc_folder_input = st.query_params.get("folder", "")

    # ------------------------------------------------------------ header
    title, folder_box, new_chat, back = st.columns(
        [3.2, 1.3, 1, 1.3], vertical_alignment="bottom")
    with title:
        st.html('<div class="ip-card-title" style="font-size:1.25rem">Ask your '
                'documents</div><div class="ip-card-sub">Each folder is separate: '
                'answers come only from the PDFs of the folder number you enter, '
                'with the file and page of every source.</div>')
    with folder_box:
        typed = st.text_input("Folder number", key="doc_folder_input",
                              placeholder="e.g. 102",
                              help="Required. Only the PDFs in documents\\<number> "
                                   "are searched.")

    folder_id, folder_error = None, None
    try:
        folder_id = clean_folder_id(typed)
    except FolderError as exc:
        folder_error = str(exc)
    if folder_id and st.query_params.get("folder") != folder_id:
        st.query_params["folder"] = folder_id
    messages = histories.setdefault(folder_id or "", [])

    with new_chat:
        if st.button("New chat", key="doc_new_chat", icon=":material/add_comment:",
                     disabled=not messages, use_container_width=True):
            histories[folder_id or ""] = []
            st.rerun()
    with back:
        if st.button("Back to Power BI", key="doc_back", icon=":material/arrow_back:",
                     use_container_width=True):
            st.session_state.view = "chat"
            st.query_params.clear()
            st.rerun()

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
        if changes:
            st.caption(", ".join(f"{len(work[k])} {k}" for k in
                                 ("new", "changed", "deleted") if work[k])
                       + " file(s) since the last indexing.")
        index_now = st.button("Index now" if changes else "Index is up to date",
                              key="doc_index", type="primary" if changes else "secondary",
                              disabled=not (changes and can_index),
                              icon=":material/sync:", use_container_width=True)
        # New or changed files are indexed automatically once per session.
        auto = (changes and can_index and not st.session_state.get("doc_auto_done"))
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
            st.info("Enter a folder number above to see and search its documents.",
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
            st.caption("Reads every PDF in every folder again. Needed only after "
                       "changing the embedding model or passage size in Admin "
                       "settings.")
            if st.button("Rebuild", key="doc_rebuild", disabled=not can_index):
                _run_index(service, True, progress_slot)

    # -------------------------------------------------------------- chat
    with chat_column, st.container(border=True, key="doc_chat_card"):
        history = st.container(height=settings["interface"]["chat_height"] - 60,
                               border=False, key="doc_chat_scroll")
        with history:
            if not messages:
                if folder_ready:
                    st.html(f'<div class="ip-welcome"><h3>Ask about folder {folder_id}'
                            '</h3><p>For example: <i>What does the policy say about '
                            'refunds?</i> or <i>Summarise the main findings.</i></p></div>')
                else:
                    st.html('<div class="ip-welcome"><h3>Enter a folder number</h3>'
                            '<p>Questions are answered only from the PDFs of the '
                            'folder you enter (for example <b>102</b> for '
                            '<code>documents\\102</code>).</p></div>')
            for index, message in enumerate(messages):
                if message["role"] == "user":
                    with st.chat_message("user", avatar=":material/person:"):
                        st.markdown(message["content"])
                else:
                    with st.chat_message("assistant", avatar=":material/description:"):
                        _render_answer(message, folder, config["show_excerpts"],
                                       f"{folder_id}-{index}", config["read_aloud"])

        question = st.chat_input(
            f"Ask a question about folder {folder_id}..." if folder_ready
            else "Enter a folder number first", key="doc_chat_input",
            disabled=not folder_ready)

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
