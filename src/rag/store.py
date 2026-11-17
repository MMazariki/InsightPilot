"""The index on disk: manifest, passages and vectors, written atomically.

    rag_index/manifest.json  index settings and one entry per PDF
    rag_index/chunks.jsonl   one passage per line, in vector-row order
    rag_index/vectors.npy    float32, one normalised row per passage
"""

import json
import os
from pathlib import Path

import numpy as np

INDEX_VERSION = 1


def _write_atomic(path, write):
    temp = path.with_name(path.name + ".tmp")
    write(temp)
    os.replace(temp, path)


class Store:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.manifest = {"version": INDEX_VERSION, "embedding_model": None,
                         "dimension": None, "files": {}}
        self.chunks = []
        self.vectors = np.zeros((0, 0), dtype=np.float32)

    # -------------------------------------------------------------- disk

    @property
    def _paths(self):
        return (self.folder / "manifest.json", self.folder / "chunks.jsonl",
                self.folder / "vectors.npy")

    def load(self):
        manifest, chunks, vectors = self._paths
        if not manifest.exists():
            return self
        self.manifest = json.loads(manifest.read_text(encoding="utf-8"))
        self.chunks = [json.loads(line) for line in
                       chunks.read_text(encoding="utf-8").splitlines() if line] \
            if chunks.exists() else []
        self.vectors = np.load(vectors) if vectors.exists() else \
            np.zeros((0, 0), dtype=np.float32)
        if len(self.chunks) != len(self.vectors):
            # A broken index is rebuilt rather than half-used.
            self.manifest["files"] = {}
            self.chunks = []
            self.vectors = np.zeros((0, 0), dtype=np.float32)
        return self

    def save(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        manifest, chunks, vectors = self._paths

        def write_vectors(temp):
            with open(temp, "wb") as handle:
                np.save(handle, self.vectors)

        _write_atomic(vectors, write_vectors)
        _write_atomic(chunks, lambda temp: temp.write_text(
            "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in self.chunks),
            encoding="utf-8"))
        _write_atomic(manifest, lambda temp: temp.write_text(
            json.dumps(self.manifest, indent=2, ensure_ascii=False), encoding="utf-8"))

    # ------------------------------------------------------------ editing

    def matches_model(self, model):
        return self.manifest.get("embedding_model") in (None, model)

    def reset(self, model):
        self.manifest = {"version": INDEX_VERSION, "embedding_model": model,
                         "dimension": None, "files": {}}
        self.chunks = []
        self.vectors = np.zeros((0, 0), dtype=np.float32)

    def remove_file(self, key):
        self.manifest["files"].pop(key, None)
        keep = [i for i, c in enumerate(self.chunks) if c["file_key"] != key]
        if len(keep) != len(self.chunks):
            self.chunks = [self.chunks[i] for i in keep]
            self.vectors = self.vectors[keep] if len(keep) else \
                np.zeros((0, self.vectors.shape[1] if self.vectors.ndim == 2 else 0),
                         dtype=np.float32)

    def add_file(self, key, entry, chunks, vectors):
        self.remove_file(key)
        for chunk in chunks:
            chunk["file_key"] = key
        if len(chunks):
            if self.vectors.size == 0:
                self.vectors = vectors.astype(np.float32)
            else:
                self.vectors = np.vstack([self.vectors, vectors.astype(np.float32)])
            self.manifest["dimension"] = int(vectors.shape[1])
            self.chunks.extend(chunks)
        self.manifest["files"][key] = entry

    def set_status(self, key, entry):
        """Record a file that has no passages (skipped or failed)."""
        self.remove_file(key)
        self.manifest["files"][key] = entry

    # -------------------------------------------------------------- stats

    def stats(self):
        files = self.manifest["files"].values()
        return {
            "files": len(self.manifest["files"]),
            "indexed": sum(1 for f in files if f.get("status") == "indexed"),
            "pages": sum(f.get("pages", 0) for f in files if f.get("status") == "indexed"),
            "chunks": len(self.chunks),
        }
