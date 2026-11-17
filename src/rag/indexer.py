"""Scan the PDF folder and keep the index in step with it.

Only new and changed files are read; deleted files are dropped. The index
is saved after every file, so a stopped run keeps what it finished. A lock
file stops two runs (two browser tabs) from writing at once.
"""

import datetime as dt
import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path

from src.rag.chunk import chunk_pages
from src.rag.extract import ExtractError, extract_pages
from src.rag.store import Store

LOCK_NAME = "index.lock"
STALE_LOCK_SECONDS = 3600


@dataclass
class FileInfo:
    key: str      # path relative to the folder, forward slashes
    path: Path
    size: int
    mtime: float


def scan(folder, recursive=True):
    folder = Path(folder)
    if not folder.is_dir():
        return []
    pattern = "**/*" if recursive else "*"
    found = []
    for path in folder.glob(pattern):
        if path.is_file() and path.suffix.lower() == ".pdf":
            stat = path.stat()
            found.append(FileInfo(path.relative_to(folder).as_posix(), path,
                                  stat.st_size, stat.st_mtime))
    return sorted(found, key=lambda f: f.key.lower())


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def plan(store, files):
    """Return ``{"new", "changed", "deleted", "unchanged"}`` (lists of keys)."""

    known = store.manifest["files"]
    on_disk = {f.key: f for f in files}
    result = {"new": [], "changed": [], "deleted": [], "unchanged": []}
    for key, info in on_disk.items():
        entry = known.get(key)
        if entry is None:
            result["new"].append(key)
        elif entry.get("size") != info.size or abs(entry.get("mtime", 0) - info.mtime) > 1:
            result["changed"].append(key)
        else:
            result["unchanged"].append(key)
    result["deleted"] = sorted(set(known) - set(on_disk))
    return result


class IndexBusy(Exception):
    pass


class _Lock:
    def __init__(self, folder):
        self.path = Path(folder) / LOCK_NAME

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and time.time() - self.path.stat().st_mtime < STALE_LOCK_SECONDS:
            raise IndexBusy("Indexing is already running (another tab?).")
        self.path.write_text(str(os.getpid()), encoding="utf-8")
        return self

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except OSError:
            pass


def index_settings(embedding_model, chunk_chars=1000, overlap_chars=150,
                   min_page_chars=20, header_share=0.6):
    """The settings an index depends on; recorded in the manifest."""
    return {"embedding_model": embedding_model, "chunk_chars": chunk_chars,
            "overlap_chars": overlap_chars, "min_page_chars": min_page_chars,
            "header_share": header_share}


def needs_rebuild(store, settings):
    """True when the index was built with different settings (an empty
    index never needs one)."""
    if not store.manifest.get("files"):
        return False
    built = store.manifest.get("index_settings")
    if built is None:  # index from before settings were recorded
        return not store.matches_model(settings["embedding_model"])
    return built != settings


def index_folder(folder, index_dir, embedder, chunk_chars=1000, overlap_chars=150,
                 recursive=True, rebuild=False, progress=None, min_page_chars=20,
                 header_share=0.6):
    """Bring the index up to date. ``progress(message, done, total)`` is
    called as files are processed. Returns ``(store, summary)``. A change
    in any setting the index depends on rebuilds it."""

    settings = index_settings(embedder.model, chunk_chars, overlap_chars,
                              min_page_chars, header_share)
    with _Lock(index_dir):
        store = Store(index_dir).load()
        if rebuild or needs_rebuild(store, settings):
            store.reset(embedder.model)
        store.manifest["embedding_model"] = embedder.model
        store.manifest["index_settings"] = settings
        store.manifest["folder"] = str(folder)

        files = scan(folder, recursive)
        work = plan(store, files)
        by_key = {f.key: f for f in files}
        summary = {"added": 0, "updated": 0, "removed": len(work["deleted"]),
                   "skipped": 0, "failed": 0, "unchanged": len(work["unchanged"])}

        for key in work["deleted"]:
            store.remove_file(key)
        if work["deleted"]:
            store.save()

        todo = work["new"] + work["changed"]
        for number, key in enumerate(todo, start=1):
            info = by_key[key]
            if progress:
                progress(f"Reading {key}", number - 1, len(todo))
            entry = {"size": info.size, "mtime": info.mtime,
                     "indexed_at": dt.datetime.now().isoformat(timespec="seconds")}
            try:
                digest = sha256(info.path)
                entry["sha256"] = digest
                pages, page_count = extract_pages(info.path, min_page_chars,
                                                  header_share)
                entry["pages"] = page_count
                chunks = chunk_pages(pages, key, digest, chunk_chars, overlap_chars)

                def embed_progress(done, total, key=key, number=number):
                    if progress:
                        progress(f"Indexing {key} ({done}/{total} passages)",
                                 number - 1 + done / max(total, 1), len(todo))

                vectors = embedder.embed_documents([c["text"] for c in chunks],
                                                   embed_progress)
                entry.update(status="indexed", chunks=len(chunks), error="")
                store.add_file(key, entry, chunks, vectors)
                summary["updated" if key in work["changed"] else "added"] += 1
            except ExtractError as exc:
                entry.update(status=exc.status, chunks=0, error=str(exc))
                store.set_status(key, entry)
                summary["skipped"] += 1
            except Exception as exc:  # one bad file never stops the run
                if exc.__class__.__name__ == "EmbedError":
                    raise
                entry.update(status="error", chunks=0, error=str(exc)[:300])
                store.set_status(key, entry)
                summary["failed"] += 1
            store.save()
        if progress:
            progress("Done", len(todo), len(todo))
        return store, summary
