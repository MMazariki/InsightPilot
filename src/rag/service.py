"""One object the page uses: settings -> index, search and answers."""

from pathlib import Path

from src.rag.answer import answer, ollama_chat
from src.rag.embed import OllamaEmbedder
from src.rag.indexer import index_folder, plan, scan
from src.rag.retrieve import Retriever
from src.rag.store import Store

ROOT = Path(__file__).resolve().parents[2]
INDEX_DIR = ROOT / "rag_index"
DEFAULT_FOLDER = ROOT / "documents"


def resolve_folder(setting):
    """The PDF folder: the setting (absolute, or relative to the project),
    else ``documents`` in the project."""

    if not setting:
        return DEFAULT_FOLDER
    path = Path(setting)
    return path if path.is_absolute() else ROOT / path


class DocumentService:
    def __init__(self, config, index_dir=INDEX_DIR, embedder=None, chat=None):
        self.config = config
        self.folder = resolve_folder(config.get("folder"))
        self.index_dir = Path(index_dir)
        self.embedder = embedder or OllamaEmbedder(
            config["embedding_model"], config["ollama_url"])
        self._chat = chat
        self.store = Store(self.index_dir).load()
        self._retriever = None

    # ----------------------------------------------------------- library

    def pending(self):
        """What indexing would do now (cheap: sizes and dates only)."""
        files = scan(self.folder, self.config["recursive"])
        work = plan(self.store, files)
        if not self.store.matches_model(self.embedder.model):
            work["new"] = [f.key for f in files]
            work["changed"] = work["unchanged"] = []
        return files, work

    def index(self, rebuild=False, progress=None):
        self.store, summary = index_folder(
            self.folder, self.index_dir, self.embedder,
            self.config["chunk_chars"], self.config["overlap_chars"],
            self.config["recursive"], rebuild, progress)
        self._retriever = None
        return summary

    def library(self):
        return self.store.manifest.get("files", {})

    # ------------------------------------------------------------ asking

    @property
    def retriever(self):
        if self._retriever is None:
            self._retriever = Retriever(self.store, self.embedder)
        return self._retriever

    def chat(self, messages):
        if self._chat:
            return self._chat(messages)
        return ollama_chat(messages, self.config["answer_model"],
                           self.config["ollama_url"], self.config["answer_timeout"])

    def ask(self, question, history=(), files=None):
        # Follow-up questions search with the previous question's words too.
        query = question
        previous = [t["content"] for t in history if t["role"] == "user"]
        if previous and len(question.split()) < 8:
            query = f"{previous[-1]} {question}"
        hits, best = self.retriever.search(
            query, self.config["top_k"], self.config["min_score"], files)
        result = answer(question, hits, self.chat, history)
        result["best_score"] = round(best, 3)
        result["retrieved"] = len(hits)
        return result
