from __future__ import annotations

import json
import os
import time
from pathlib import Path

from unclog.scan.session import latest_session_path, mcp_invocation_counts

# -- latest_session_path ----------------------------------------------------


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


# -- mcp_invocation_counts --------------------------------------------------


_NOW = 1_700_000_000.0  # arbitrary fixed reference for window math


def _tool_use_record(name: str) -> dict[str, object]:
    """Shape a Claude Code-style assistant turn carrying a single tool_use."""
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "abc", "name": name, "input": {}}],
        },
    }


def _write_jsonl(path: Path, records: list[dict[str, object]], *, mtime: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def test_mcp_invocation_counts_returns_empty_for_missing_dir(tmp_path: Path) -> None:
    assert mcp_invocation_counts(tmp_path / "nope", now_ts=_NOW) == {}


def test_mcp_invocation_counts_counts_tool_use_in_parent_session(tmp_path: Path) -> None:
    project = tmp_path / "-Users-tom-proj"
    _write_jsonl(
        project / "session.jsonl",
        [_tool_use_record("mcp__notion__create_page")] * 3
        + [_tool_use_record("mcp__github__list_repos")],
        mtime=_NOW - 86_400,
    )
    counts = mcp_invocation_counts(tmp_path, now_ts=_NOW)
    assert dict(counts) == {"notion": 3, "github": 1}


def test_mcp_invocation_counts_walks_subagent_jsonls(tmp_path: Path) -> None:
    """Subagent JSONLs sit one level deep under <session-id>/subagents/.

    On real installs roughly half of all MCP invocations show up here.
    A non-recursive walk would silently undercount, which is the bug
    we're guarding against.
    """
    project = tmp_path / "-Users-tom-proj"
    _write_jsonl(
        project / "abc-123.jsonl",
        [_tool_use_record("mcp__notion__create_page")],
        mtime=_NOW - 3_600,
    )
    _write_jsonl(
        project / "abc-123" / "subagents" / "agent-xyz.jsonl",
        [_tool_use_record("mcp__notion__create_page")] * 2
        + [_tool_use_record("mcp__polymarket-docs__search")],
        mtime=_NOW - 3_600,
    )
    counts = mcp_invocation_counts(tmp_path, now_ts=_NOW)
    assert dict(counts) == {"notion": 3, "polymarket-docs": 1}


def test_mcp_invocation_counts_partitions_server_with_dashes(tmp_path: Path) -> None:
    """Server names can contain dashes; partition is on ``__``, not ``-``."""
    project = tmp_path / "-Users-tom-proj"
    _write_jsonl(
        project / "session.jsonl",
        [_tool_use_record("mcp__polymarket-docs__SearchPolymarketDocumentation")],
        mtime=_NOW - 86_400,
    )
    counts = mcp_invocation_counts(tmp_path, now_ts=_NOW)
    assert dict(counts) == {"polymarket-docs": 1}


def test_mcp_invocation_counts_handles_tool_with_underscores(tmp_path: Path) -> None:
    """Tool names can contain single underscores; the partition is ``__``."""
    project = tmp_path / "-Users-tom-proj"
    _write_jsonl(
        project / "session.jsonl",
        [_tool_use_record("mcp__Claude_in_Chrome__tabs_context_mcp")],
        mtime=_NOW - 86_400,
    )
    counts = mcp_invocation_counts(tmp_path, now_ts=_NOW)
    assert dict(counts) == {"Claude_in_Chrome": 1}


def test_mcp_invocation_counts_skips_non_mcp_tool_use(tmp_path: Path) -> None:
    project = tmp_path / "-Users-tom-proj"
    _write_jsonl(
        project / "session.jsonl",
        [
            _tool_use_record("Bash"),
            _tool_use_record("Read"),
            _tool_use_record("mcp__notion__create_page"),
        ],
        mtime=_NOW - 86_400,
    )
    counts = mcp_invocation_counts(tmp_path, now_ts=_NOW)
    assert dict(counts) == {"notion": 1}


def test_mcp_invocation_counts_respects_window_days(tmp_path: Path) -> None:
    project = tmp_path / "-Users-tom-proj"
    _write_jsonl(
        project / "in-window.jsonl",
        [_tool_use_record("mcp__notion__create_page")],
        mtime=_NOW - 5 * 86_400,
    )
    _write_jsonl(
        project / "stale.jsonl",
        [_tool_use_record("mcp__notion__create_page")] * 99,
        mtime=_NOW - 90 * 86_400,
    )
    counts = mcp_invocation_counts(tmp_path, window_days=30, now_ts=_NOW)
    assert dict(counts) == {"notion": 1}


def test_mcp_invocation_counts_tolerates_malformed_lines(tmp_path: Path) -> None:
    project = tmp_path / "-Users-tom-proj"
    path = project / "session.jsonl"
    project.mkdir(parents=True)
    path.write_text(
        "this is not json\n"
        + json.dumps(_tool_use_record("mcp__notion__create_page"))
        + "\n{not valid\n"
        + json.dumps(_tool_use_record("mcp__github__list_repos"))
        + "\n",
        encoding="utf-8",
    )
    os.utime(path, (_NOW - 3_600, _NOW - 3_600))
    counts = mcp_invocation_counts(tmp_path, now_ts=_NOW)
    assert dict(counts) == {"notion": 1, "github": 1}


def test_mcp_invocation_counts_does_not_match_user_message_mentions(tmp_path: Path) -> None:
    """A user typing 'use mcp__notion__...' is not an invocation."""
    project = tmp_path / "-Users-tom-proj"
    record = {
        "type": "user",
        "message": {
            "role": "user",
            "content": "please call mcp__notion__create_page for me",
        },
    }
    _write_jsonl(project / "session.jsonl", [record], mtime=_NOW - 3_600)
    counts = mcp_invocation_counts(tmp_path, now_ts=_NOW)
    assert dict(counts) == {}


def test_mcp_invocation_counts_returns_mapping_proxy(tmp_path: Path) -> None:
    """Returned mapping is read-only so callers can't mutate the cache."""
    counts = mcp_invocation_counts(tmp_path, now_ts=_NOW)
    import pytest

    with pytest.raises(TypeError):
        counts["x"] = 1  # type: ignore[index]
