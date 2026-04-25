"""Read Claude Code session JSONLs.

Three helpers:

- :func:`latest_session_path` / :func:`latest_session_across_projects`
  find the most recent ``*.jsonl`` for a project or across every
  project directory.
- :func:`mcp_session_tokens` reads the first ``tools`` array in a JSONL
  and tokenizes each ``mcp__<server>__<tool>`` schema. Modern Claude
  Code builds usually omit the array entirely, in which case the
  function returns ``{}`` and the picker honestly renders ``— tok`` for
  every MCP. The full per-MCP cost is not recoverable from session data
  alone — it lives only inside the running MCP server's schema.
- :func:`mcp_invocation_counts` walks every JSONL (parent *and*
  subagent — they live under ``<session-id>/subagents/agent-*.jsonl``
  and a top-level walk would miss them) modified within ``window_days``
  and tallies ``mcp__<server>__*`` ``tool_use`` blocks per server.
  Cheap byte-level pre-filter keeps the walk fast on installs with
  thousands of session files.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from unclog.scan.tokens import TiktokenCounter

# How far into a JSONL we're willing to look for the tools array.
# The schema lands on the first user turn, so this is generous.
MAX_SESSION_RECORDS = 25

# Default time window for invocation counting. 30 days is long enough
# to capture infrequent-but-legitimate MCP use and short enough that
# stale servers actually surface as unused.
DEFAULT_INVOCATION_WINDOW_DAYS = 30

_MCP_TOOL_PREFIX = "mcp__"
# Pre-filter substring used by mcp_invocation_counts. Matches any byte
# sequence containing ``mcp__``; we intentionally ignore the surrounding
# JSON shape (key order, whitespace) and let the structural parse below
# reject anything that isn't actually a ``tool_use`` block. False
# positives cost a json.loads call; false negatives would silently
# undercount, so this side errs permissive.
_MCP_TOOL_PREFIX_BYTES = b"mcp__"
_SECONDS_PER_DAY = 86_400


def latest_session_path(project_session_dir: Path) -> Path | None:
    """Return the most recently modified ``*.jsonl`` in the directory, or None."""
    if not project_session_dir.is_dir():
        return None
    candidates = [p for p in project_session_dir.iterdir() if p.suffix == ".jsonl" and p.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def latest_session_across_projects(projects_dir: Path) -> Path | None:
    """Return the most recent session JSONL across every project directory."""
    if not projects_dir.is_dir():
        return None
    best: tuple[float, Path] | None = None
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        candidate = latest_session_path(project_dir)
        if candidate is None:
            continue
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        if best is None or mtime > best[0]:
            best = (mtime, candidate)
    return best[1] if best else None


def _first_tools_array(session_path: Path) -> list[dict[str, object]]:
    """Read the bounded prefix and return the first non-empty ``tools`` list.

    Accepts two shapes seen in the wild: a record with ``tools`` at the
    top level, or one where ``tools`` is nested inside ``message``.
    Malformed lines are skipped silently; an unreadable file returns
    an empty list.
    """
    try:
        with session_path.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= MAX_SESSION_RECORDS:
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                tools = record.get("tools")
                if isinstance(tools, list) and tools:
                    return [t for t in tools if isinstance(t, dict)]
                message = record.get("message")
                if isinstance(message, dict):
                    nested = message.get("tools")
                    if isinstance(nested, list) and nested:
                        return [t for t in nested if isinstance(t, dict)]
    except OSError:
        return []
    return []


def mcp_session_tokens(session_path: Path | None) -> dict[str, int]:
    """Sum per-server tokens from the first tools array in ``session_path``.

    Returns ``{}`` when no session is available or no tools array is
    present (modern Claude Code JSONLs usually omit schemas).
    """
    if session_path is None:
        return {}

    counter = TiktokenCounter()
    per_server: dict[str, int] = {}
    for tool in _first_tools_array(session_path):
        name = tool.get("name")
        if not isinstance(name, str) or not name.startswith(_MCP_TOOL_PREFIX):
            continue
        remainder = name[len(_MCP_TOOL_PREFIX) :]
        server, _, _ = remainder.partition("__")
        if not server:
            continue
        blob = json.dumps(tool, separators=(",", ":"))
        per_server[server] = per_server.get(server, 0) + counter.count(blob)
    return per_server


def mcp_invocation_counts(
    projects_dir: Path,
    *,
    window_days: int = DEFAULT_INVOCATION_WINDOW_DAYS,
    now_ts: float | None = None,
) -> Mapping[str, int]:
    """Count ``mcp__<server>__*`` ``tool_use`` blocks across recent session JSONLs.

    Walks every ``*.jsonl`` under ``projects_dir`` (recursively, so
    subagent JSONLs at ``<session-id>/subagents/agent-*.jsonl`` are
    included alongside parent sessions) modified within the last
    ``window_days``. Returns a server-name → invocation-count mapping.

    Pre-filters lines on the bytes ``"name":"mcp__`` before invoking
    ``json.loads`` — most lines in a typical session are user/assistant
    text and never match, so we skip JSON parsing on the hot path.
    """
    if not projects_dir.is_dir():
        return MappingProxyType({})

    cutoff = (now_ts if now_ts is not None else time.time()) - window_days * _SECONDS_PER_DAY
    counts: dict[str, int] = {}
    for path in projects_dir.rglob("*.jsonl"):
        try:
            if not path.is_file() or path.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        _accumulate_mcp_invocations(path, counts)
    return MappingProxyType(counts)


def _accumulate_mcp_invocations(path: Path, counts: dict[str, int]) -> None:
    """Add this JSONL's MCP invocation counts into ``counts`` in place."""
    try:
        with path.open("rb") as f:
            for raw in f:
                # Cheap pre-filter: most lines never mention an MCP tool
                # name, so we skip json.loads on the hot path.
                if _MCP_TOOL_PREFIX_BYTES not in raw:
                    continue
                try:
                    record = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                message = record.get("message")
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    name = block.get("name")
                    if not isinstance(name, str) or not name.startswith(_MCP_TOOL_PREFIX):
                        continue
                    server = name[len(_MCP_TOOL_PREFIX) :].partition("__")[0]
                    if server:
                        counts[server] = counts.get(server, 0) + 1
    except OSError:
        return
