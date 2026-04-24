"""Parse Claude Code session JSONLs for MCP token attribution.

Session JSONLs are the only source of attribution for MCP tokens in 0.2.
The interesting signal is the first non-empty ``tools`` array in the
JSONL: each ``mcp__<server>__<tool>`` entry tells us how much a given
server is costing per turn. Modern Claude Code builds often omit
schemas, in which case :func:`mcp_session_tokens` returns ``{}`` and the
picker falls back to ``— tok`` rows.
"""

from __future__ import annotations

import json
from pathlib import Path

from unclog.scan.tokens import TiktokenCounter

# How far into a JSONL we're willing to look for the tools array.
# The schema lands on the first user turn, so this is generous.
MAX_SESSION_RECORDS = 25

_MCP_TOOL_PREFIX = "mcp__"


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
