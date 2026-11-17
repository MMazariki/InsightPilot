"""Pages -> overlapping passages that keep their page numbers."""

import hashlib
import re

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _pieces(text):
    """Paragraphs, then sentences, as the smallest units to pack."""

    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = " ".join(paragraph.split())
        if paragraph:
            yield from (s for s in _SENTENCE_END.split(paragraph) if s)
            yield "\n"


def _split_long(piece, size):
    words, out, current = piece.split(" "), [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > size:
            out.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        out.append(current)
    return out


def chunk_pages(pages, file_name, file_hash, size=1000, overlap=150):
    """Return chunk records ``{id, file, page_start, page_end, text}``.

    Passages of about ``size`` characters are built from whole sentences;
    each starts with roughly ``overlap`` characters from the end of the
    previous one on the same page. A new page always starts a new passage,
    so every citation names one page.
    """

    units = []  # (text, page)
    for page in pages:
        for piece in _pieces(page.text):
            if piece == "\n":
                if units and units[-1][0] != "\n":
                    units.append(("\n", page.number))
                continue
            for part in (_split_long(piece, size) if len(piece) > size else [piece]):
                units.append((part, page.number))

    chunks, current, pages_in = [], [], []

    def flush():
        text = " ".join(t for t, _ in current if t != "\n")
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            ordinal = len(chunks)
            chunks.append({
                "id": hashlib.sha1(f"{file_hash}:{ordinal}".encode()).hexdigest()[:16],
                "file": file_name,
                "page_start": min(pages_in),
                "page_end": max(pages_in),
                "text": text,
            })

    length = 0
    for text, page in units:
        if text == "\n":
            continue
        # A new page starts a new passage, so citations name one page.
        if current and page != current[-1][1]:
            flush()
            current, pages_in, length = [], [], 0
        if current and length + len(text) + 1 > size:
            flush()
            # Carry the tail of this passage into the next one.
            carried, carried_len = [], 0
            for prev_text, prev_page in reversed(current):
                if prev_text == "\n":
                    continue
                if carried_len + len(prev_text) > overlap:
                    break
                carried.insert(0, (prev_text, prev_page))
                carried_len += len(prev_text) + 1
            current = carried
            pages_in = [p for _, p in carried]
            length = carried_len
        current.append((text, page))
        pages_in.append(page)
        length += len(text) + 1
    if current:
        flush()
    return chunks
