"""PDF -> text per page, cleaned for search."""

import re
from collections import Counter
from dataclasses import dataclass

MIN_PAGE_CHARS = 20
REPEATED_LINE_SHARE = 0.6


class ExtractError(Exception):
    """A PDF that cannot be read; ``status`` says why in a word."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


@dataclass
class Page:
    number: int  # 1-based
    text: str


def _clean(text):
    text = text.replace("­", "")                  # soft hyphens
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)       # manage-\nment
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _drop_repeated_lines(pages, share=REPEATED_LINE_SHARE):
    """Headers and footers: short lines found on at least ``share`` of the
    pages."""

    if len(pages) < 3:
        return pages
    counts = Counter()
    for page in pages:
        counts.update({line.strip() for line in page.text.split("\n")
                       if 0 < len(line.strip()) <= 80})
    repeated = {line for line, n in counts.items()
                if n >= share * len(pages)
                and not re.fullmatch(r"\d+", line)}
    page_numbers = re.compile(r"^(page\s*)?\d+(\s*(of|/)\s*\d+)?$", re.I)
    cleaned = []
    for page in pages:
        lines = [line for line in page.text.split("\n")
                 if line.strip() not in repeated
                 and not page_numbers.match(line.strip())]
        cleaned.append(Page(page.number, "\n".join(lines).strip()))
    return cleaned


def extract_pages(path, min_page_chars=MIN_PAGE_CHARS, header_share=REPEATED_LINE_SHARE):
    """Return ``(pages with text, page count)``. Raises ``ExtractError`` with
    status ``password`` / ``no_text`` / ``unreadable``."""

    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise ExtractError("password", "password protected")
            except ExtractError:
                raise
            except Exception as exc:
                raise ExtractError("password", "password protected") from exc
        pages = []
        for index, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            pages.append(Page(index, _clean(text)))
    except ExtractError:
        raise
    except (PdfReadError, OSError, ValueError, KeyError, TypeError) as exc:
        raise ExtractError("unreadable", f"cannot be read: {exc}") from exc

    pages = _drop_repeated_lines(pages, header_share)
    with_text = [p for p in pages if len(p.text) >= max(1, min_page_chars)]
    if not with_text:
        raise ExtractError("no_text", "no text layer (scanned?)")
    return with_text, len(pages)
