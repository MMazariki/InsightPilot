"""Monthly log files: the app's JSON-lines logs do not grow forever.

Before an entry is written, a log last written in an earlier month is
renamed with that month (``actions.jsonl`` -> ``actions-2026-10.jsonl``),
so the current file holds only this month and stays quick to read (the
daily email limit and the History tables read it). Older months stay on
disk unchanged; nothing is deleted unless ``keep`` is given.

Used by the action log, the security log and the alerts log.
"""

import datetime as dt
import re
from pathlib import Path


def rotate(path, now=None, keep=None):
    """Start a new file for a new month (see the module docstring).
    ``keep``: keep only that many monthly files (``None``: keep all)."""

    path = Path(path)
    now = now or dt.datetime.now()
    try:
        written = dt.datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None
    if (written.year, written.month) >= (now.year, now.month):
        return None
    archive = path.with_name(f"{path.stem}-{written:%Y-%m}{path.suffix}")
    if archive.exists():                      # never overwrite: add to it
        with archive.open("a", encoding="utf-8") as out:
            out.write(path.read_text(encoding="utf-8"))
        path.unlink()
    else:
        path.rename(archive)
    if keep is not None:
        for old in archives(path)[:-keep] if keep > 0 else archives(path):
            try:
                old.unlink()
            except OSError:
                pass
    return archive


def archives(path):
    """Monthly files of ``path``, oldest first."""

    path = Path(path)
    pattern = re.compile(re.escape(path.stem) + r"-\d{4}-\d{2}" + re.escape(path.suffix))
    return sorted(p for p in path.parent.glob(f"{path.stem}-*{path.suffix}")
                  if pattern.fullmatch(p.name))
