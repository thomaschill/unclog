from __future__ import annotations

import json
import os
import time
from pathlib import Path

from unclog.scan.session import (
    SessionSystemBlock,
    latest_session_path,
    load_session_system_block,
)
from unclog.scan.tokens import TiktokenCounter


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )


def test_latest_session_path_picks_newest_mtime(tmp_path: Path) -> None:
    older = tmp_path / "older.jsonl"
    newer = tmp_path / "newer.jsonl"
    older.write_text("{}\n", encoding="utf-8")
    newer.write_text("{}\n", encoding="utf-8")
    # Force mtime ordering regardless of filesystem resolution.
    os.utime(older, (time.time() - 10, time.time() - 10))
    os.utime(newer, (time.time(), time.time()))

    assert latest_session_path(tmp_path) == newer


def test_latest_session_path_returns_none_when_missing(tmp_path: Path) -> None:
    assert latest_session_path(tmp_path / "nope") is None


def test_latest_session_path_ignores_non_jsonl(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("hi", encoding="utf-8")
    assert latest_session_path(tmp_path) is None


def test_load_extracts_system_prompt_from_type_system_record(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "system", "content": "You are Claude Code. Follow CLAUDE.md."},
            {"type": "user", "content": "hi"},
        ],
    )
    block = load_session_system_block(path, TiktokenCounter())
    assert block is not None
    assert "CLAUDE.md" in block.system_text
    assert block.system_tokens > 0
    assert block.tools_tokens == 0


def test_load_extracts_system_from_sibling_field(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    _write_jsonl(
        path,
        [
            {
                "type": "user",
                "system": [{"type": "text", "text": "system preamble here"}],
                "content": "hello",
            }
        ],
    )
    block = load_session_system_block(path, TiktokenCounter())
    assert block is not None
    assert "system preamble" in block.system_text


def test_load_extracts_tools_and_counts_them(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "system", "content": "sys"},
            {
                "type": "user",
                "tools": [
                    {"name": "github__list_repos", "description": "...", "input_schema": {}},
                    {"name": "notion__create_page", "description": "...", "input_schema": {}},
                ],
            },
        ],
    )
    block = load_session_system_block(path, TiktokenCounter())
    assert block is not None
    assert block.tools_tokens > 0
    assert "github__list_repos" in block.tools_json


def test_load_tolerates_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        "this is not json\n"
        + json.dumps({"type": "system", "content": "real system"})
        + "\n{broken\n",
        encoding="utf-8",
    )
    block = load_session_system_block(path, TiktokenCounter())
    assert block is not None
    assert "real system" in block.system_text


def test_load_returns_none_when_no_system_or_tools(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "user", "content": "hi"},
            {"type": "assistant", "content": "hello"},
        ],
    )
    assert load_session_system_block(path, TiktokenCounter()) is None


def test_load_returns_none_on_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text("", encoding="utf-8")
    assert load_session_system_block(path, TiktokenCounter()) is None


def test_total_tokens_sums_system_and_tools() -> None:
    block = SessionSystemBlock(
        session_path=Path("/x.jsonl"),
        system_text="hi",
        tools_json="[]",
        tools=(),
        system_tokens=5,
        tools_tokens=7,
    )
    assert block.total_tokens == 12


def test_load_exposes_parsed_tools(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    _write_jsonl(
        path,
        [
            {
                "type": "user",
                "tools": [
                    {"name": "mcp__github__list_repos", "description": "...", "input_schema": {}},
                    {"name": "Read", "description": "built-in", "input_schema": {}},
                ],
            },
        ],
    )
    block = load_session_system_block(path, TiktokenCounter())
    assert block is not None
    assert len(block.tools) == 2
    assert block.tools[0]["name"] == "mcp__github__list_repos"
