"""One object the page uses: settings -> index, search and answers.

Segregation: documents are grouped by their top-level subfolder (for
example ``documents/102/...``). A question is answered only from the
folder it names; without a valid folder nothing is searched. PDFs placed
directly in the documents folder belong to no folder and are never
searched.
"""

import re
from pathlib import Path

from src.rag.answer import answer, ollama_chat
from src.rag.embed import OllamaEmbedder
from src.rag.indexer import index_folder, index_settings, needs_rebuild, plan, scan
from src.rag.retrieve import Retriever
from src.rag.store import Store

ROOT = Path(__file__).resolve().parents[2]
INDEX_DIR = ROOT / "rag_index"
DEFAULT_FOLDER = ROOT / "documents"


FOLDER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
NO_FOLDER = "Enter a folder number to search its documents."


class FolderError(ValueError):
    """The folder number is missing, malformed or has no documents."""


def folder_of(key):
    """``"102/sub/x.pdf"`` -> ``"102"``; a file in the root -> ``None``."""
    return key.split("/", 1)[0] if "/" in key else None


def clean_folder_id(value):
    """The folder number as typed, checked: letters, digits, - and _ only
    (no paths). Returns ``None`` when empty; raises ``FolderError`` when
    malformed."""

    text = str(value or "").strip()
    if not text:
        return None
    if not FOLDER_ID.fullmatch(text):
        raise FolderError("A folder number may contain only letters, digits, "
                          "'-' and '_'.")
    return text


def resolve_folder(setting):
    """The PDF folder: the setting (absolute, or relative to the project),
    else ``documents`` in the project."""

    if not setting:
        return DEFAULT_FOLDER
    path = Path(setting)
    return path if path.is_absolute() else ROOT / path


class DocumentService:
    def __init__(self, config, index_dir=INDEX_DIR, embedder=None, chat=None):
        from src.settings import DEFAULTS

        # Missing keys (older settings files, tests) take the defaults.
        self.config = {**DEFAULTS["documents"], **config}
        config = self.config
        self.folder = resolve_folder(config.get("folder"))
        self.index_dir = Path(index_dir)
        self.embedder = embedder or OllamaEmbedder(
            config["embedding_model"], config["ollama_url"],
            config["embed_timeout"], config["embed_batch"])
        self._chat = chat
        self.store = Store(self.index_dir).load()
        self._retriever = None

    # ----------------------------------------------------------- library

    def pending(self):
        """What indexing would do now (cheap: sizes and dates only)."""
        files = scan(self.folder, self.config["recursive"])
        work = plan(self.store, files)
        if self.needs_rebuild():
            work["new"] = [f.key for f in files]
            work["changed"] = work["unchanged"] = []
            work["deleted"] = []
        return files, work

    def index_settings(self):
        c = self.config
        return index_settings(self.embedder.model, c["chunk_chars"],
                              c["overlap_chars"], c["min_page_chars"],
                              c["header_share"])

    def needs_rebuild(self):
        """The index was built with other settings (model, passage size,
        extraction thresholds): it is rebuilt at the next indexing."""
        return needs_rebuild(self.store, self.index_settings())

    def index(self, rebuild=False, progress=None):
        self.store, summary = index_folder(
            self.folder, self.index_dir, self.embedder,
            self.config["chunk_chars"], self.config["overlap_chars"],
            self.config["recursive"], rebuild, progress,
            self.config["min_page_chars"], self.config["header_share"])
        self._retriever = None
        return summary

    def library(self, folder_id=None):
        """Files of one folder (all files when ``folder_id`` is ``None``,
        for indexing status only)."""
        files = self.store.manifest.get("files", {})
        if folder_id is None:
            return files
        return {k: v for k, v in files.items() if folder_of(k) == folder_id}

    def folder_keys(self, folder_id):
        """Indexed files of the folder; raises ``FolderError`` if none."""
        folder_id = clean_folder_id(folder_id)
        if folder_id is None:
            raise FolderError(NO_FOLDER)
        keys = [k for k, v in self.library(folder_id).items()
                if v.get("status") == "indexed"]
        if not keys:
            raise FolderError(f"There are no indexed documents in folder {folder_id}.")
        return keys

    def unfoldered(self):
        """PDFs directly in the documents folder: never searchable."""
        return [k for k in self.library() if folder_of(k) is None]

    # ------------------------------------------------------------ asking

    @property
    def retriever(self):
        if self._retriever is None:
            self._retriever = Retriever(self.store, self.embedder)
        return self._retriever

    def chat(self, messages):
        if self._chat:
            return self._chat(messages)
        c = self.config
        return ollama_chat(messages, c["answer_model"], c["ollama_url"],
                           c["answer_timeout"], c["answer_num_ctx"],
                           c["answer_keep_alive"], c["answer_temperature"])

    def ask(self, question, history=(), folder_id=None, files=None):
        """Answer from the PDFs of ``folder_id`` only. ``files`` narrows the
        search further but can never reach outside the folder. Raises
        ``FolderError`` without a valid folder."""

        allowed = self.folder_keys(folder_id)
        if files:
            allowed = [k for k in allowed if k in set(files)]
            if not allowed:
                raise FolderError("None of the chosen files are in this folder.")
        # Follow-up questions search with the previous question's words too.
        query = question
        previous = [t["content"] for t in history if t["role"] == "user"]
        c = self.config
        if previous and c["history_exchanges"] and len(question.split()) < c["followup_words"]:
            query = f"{previous[-1]} {question}"
        hits, best = self.retriever.search(
            query, c["top_k"], c["min_score"], allowed, c["candidates"],
            c["keyword_search"], c["fusion_k"])
        # Defence in depth: nothing from another folder reaches the model.
        hits = [h for h in hits if h["file_key"] in set(allowed)]
        result = answer(question, hits, self.chat, history, c["system_prompt"],
                        c["not_found_text"], c["history_exchanges"])
        result["best_score"] = round(best, 3)
        result["retrieved"] = len(hits)
        return result
