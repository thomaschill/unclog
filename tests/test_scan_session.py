from __future__ import annotations

import os
import time
from pathlib import Path

from unclog.scan.session import latest_session_path


def test_latest_session_path_picks_newest_mtime(tmp_path: Path) -> None:
    older = tmp_path / "older.jsonl"
    newer = tmp_path / "newer.jsonl"
    older.write_text("{}\n", encoding="utf-8")
    newer.write_text("{}\n", encoding="utf-8")
    os.utime(older, (time.time() - 10, time.time() - 10))
    os.utime(newer, (time.time(), time.time()))

    assert latest_session_path(tmp_path) == newer


def test_latest_session_path_returns_none_when_missing(tmp_path: Path) -> None:
    assert latest_session_path(tmp_path / "nope") is None


def test_latest_session_path_ignores_non_jsonl(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("hi", encoding="utf-8")
    assert latest_session_path(tmp_path) is None
