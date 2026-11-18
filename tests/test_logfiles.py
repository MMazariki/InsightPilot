"""Monthly log files (src/logfiles.py)."""

import datetime as dt
import os

from src import logfiles


def _written(path, when):
    stamp = when.timestamp()
    os.utime(path, (stamp, stamp))


def test_new_month_starts_a_new_file(tmp_path):
    log = tmp_path / "actions.jsonl"
    log.write_text('{"a": 1}\n', encoding="utf-8")
    _written(log, dt.datetime(2026, 10, 31, 23, 0))
    archive = logfiles.rotate(log, now=dt.datetime(2026, 11, 1, 8, 0))
    assert archive.name == "actions-2026-10.jsonl" and not log.exists()
    assert archive.read_text(encoding="utf-8") == '{"a": 1}\n'
    # Same month: nothing happens.
    log.write_text('{"b": 2}\n', encoding="utf-8")
    assert logfiles.rotate(log, now=dt.datetime.fromtimestamp(log.stat().st_mtime)) is None


def test_existing_archive_is_appended_never_overwritten(tmp_path):
    log = tmp_path / "security.jsonl"
    (tmp_path / "security-2026-10.jsonl").write_text("old\n", encoding="utf-8")
    log.write_text("new\n", encoding="utf-8")
    _written(log, dt.datetime(2026, 10, 15))
    logfiles.rotate(log, now=dt.datetime(2026, 11, 2))
    assert (tmp_path / "security-2026-10.jsonl").read_text(encoding="utf-8") == "old\nnew\n"


def test_nothing_is_deleted_unless_asked(tmp_path):
    log = tmp_path / "alerts.jsonl"
    for month in range(1, 10):
        (tmp_path / f"alerts-2026-0{month}.jsonl").write_text("x\n", encoding="utf-8")
    log.write_text("y\n", encoding="utf-8")
    _written(log, dt.datetime(2026, 10, 1))
    logfiles.rotate(log, now=dt.datetime(2026, 11, 1))
    assert len(logfiles.archives(log)) == 10
    log.write_text("z\n", encoding="utf-8")
    _written(log, dt.datetime(2026, 11, 1))
    logfiles.rotate(log, now=dt.datetime(2026, 12, 1), keep=3)
    assert [p.name for p in logfiles.archives(log)] == [
        "alerts-2026-09.jsonl", "alerts-2026-10.jsonl", "alerts-2026-11.jsonl"]
