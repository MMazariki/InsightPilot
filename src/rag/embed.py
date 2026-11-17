"""Texts -> normalised vectors with an Ollama embedding model."""

import numpy as np
import requests

BATCH = 32

# Task prefixes some embedding models were trained with.
PREFIXES = {
    "nomic-embed-text": ("search_document: ", "search_query: "),
    "mxbai-embed-large": ("", "Represent this sentence for searching relevant passages: "),
}


class EmbedError(Exception):
    pass


def _prefixes(model):
    base = model.split(":")[0]
    return PREFIXES.get(base, ("", ""))


def _normalise(matrix):
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


class OllamaEmbedder:
    def __init__(self, model, base_url="http://localhost:11434", timeout=120,
                 batch=BATCH):
        self.model = model
        self.url = base_url.rstrip("/") + "/api/embed"
        self.timeout = timeout
        self.batch = max(1, int(batch))

    def _call(self, texts):
        for attempt in range(3):
            try:
                response = requests.post(
                    self.url, json={"model": self.model, "input": texts},
                    timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt == 2:
                    raise EmbedError(
                        "Could not reach Ollama for embeddings. Is it running?") from exc
                continue
            if response.status_code == 404:
                raise EmbedError(
                    f"Ollama does not have the embedding model '{self.model}'. "
                    f"Install it with: ollama pull {self.model}")
            if response.ok:
                return response.json()["embeddings"]
            if attempt == 2:
                raise EmbedError(
                    f"Ollama embedding failed (HTTP {response.status_code}): "
                    f"{response.text[:200]}")
        raise EmbedError("Ollama embedding failed.")

    def embed_documents(self, texts, progress=None):
        prefix = _prefixes(self.model)[0]
        rows = []
        for start in range(0, len(texts), self.batch):
            batch = [prefix + t for t in texts[start:start + self.batch]]
            rows.extend(self._call(batch))
            if progress:
                progress(min(start + self.batch, len(texts)), len(texts))
        if not rows:
            return np.zeros((0, 0), dtype=np.float32)
        return _normalise(np.asarray(rows, dtype=np.float32))

    def embed_query(self, text):
        prefix = _prefixes(self.model)[1]
        return _normalise(np.asarray(self._call([prefix + text]), dtype=np.float32))[0]
