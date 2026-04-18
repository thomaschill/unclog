"""Parse Claude Code session JSONLs for baseline evidence.

Two signals we try to read from session data:

1. **System prompt text + ``tools`` array.** Used to be the authoritative
   measurement of the real token footprint Claude Code sent to the model.
   As of Claude Code ~1.x (observed 2026-04), these no longer appear as
   standalone records in the JSONL — the format moved to reporting
   ``cache_creation_input_tokens`` / ``cache_read_input_tokens`` on each
   assistant turn's ``usage`` block instead. :func:`load_session_system_block`
   therefore returns ``None`` on modern sessions, and the hero falls back
   to the filesystem composition sum (which is honest: it counts what
   users can *actually* change, not Claude Code's static runtime overhead).

2. **Per-MCP invocation counts.** Still extractable — every assistant
   turn still writes ``tool_use`` blocks with the ``mcp__<server>__<tool>``
   naming convention, and :func:`count_mcp_invocations` walks them to
   feed ``unused_mcp`` / ``dead_mcp`` detectors.

The parser is intentionally permissive: bounded prefix scan, malformed
line skipping, multiple plausible shapes for the system prompt. Callers
never see exceptions — only ``None`` or empty mappings.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from unclog.scan.tokens import TokenCounter

# Cap how far into a JSONL we look for the system block. The system
# prompt lands on the first user turn, so this is generous.
MAX_RECORDS_SCANNED = 25

# Cap how many lines we'll scan when counting MCP tool_use invocations.
# Real sessions top out in the low thousands of records; this is the
# upper bound we're willing to spend reading a single JSONL.
MAX_INVOCATION_RECORDS = 50_000

_MCP_TOOL_PREFIX = "mcp__"


@dataclass(frozen=True)
class SessionSystemBlock:
    """What Claude Code actually injected at the start of a session."""

    session_path: Path
    system_text: str
    tools_json: str
    tools: tuple[dict[str, Any], ...]
    system_tokens: int
    tools_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.system_tokens + self.tools_tokens


def latest_session_path(project_session_dir: Path) -> Path | None:
    """Return the most recently modified ``*.jsonl`` in the directory, or None."""
    if not project_session_dir.is_dir():
        return None
    candidates = [p for p in project_session_dir.iterdir() if p.suffix == ".jsonl" and p.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _iter_records(path: Path, limit: int) -> list[dict[str, Any]]:
    """Read up to ``limit`` JSON objects from a JSONL, skipping malformed lines."""
    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    records.append(value)
                    if len(records) >= limit:
                        break
    except OSError:
        return []
    return records


def _extract_text_content(content: Any) -> str:
    """Flatten an Anthropic-style ``content`` field to a single string.

    Accepts:
    - plain strings
    - lists of ``{"type": "text", "text": "..."}`` blocks
    - lists of strings
    - anything else: returns ``""``
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _find_system_text(records: list[dict[str, Any]]) -> str:
    """Return the first plausible system-prompt text in ``records``."""
    for record in records:
        # Shape A: {"type": "system", "content": "..."} or {"role": "system", ...}
        if record.get("type") == "system" or record.get("role") == "system":
            text = _extract_text_content(record.get("content"))
            if text:
                return text
        # Shape B: {"system": "..."} or {"system": [...]} as a sibling of "messages"
        sys_field = record.get("system")
        if sys_field is not None:
            text = _extract_text_content(sys_field)
            if text:
                return text
        # Shape C: {"message": {"role": "system", "content": "..."}}
        message = record.get("message")
        if isinstance(message, dict):
            if message.get("role") == "system":
                text = _extract_text_content(message.get("content"))
                if text:
                    return text
            nested_system = message.get("system")
            if nested_system is not None:
                text = _extract_text_content(nested_system)
                if text:
                    return text
    return ""


def _find_tools(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the first non-empty ``tools`` array found in ``records``."""
    for record in records:
        tools = record.get("tools")
        if isinstance(tools, list) and tools:
            return [t for t in tools if isinstance(t, dict)]
        message = record.get("message")
        if isinstance(message, dict):
            nested = message.get("tools")
            if isinstance(nested, list) and nested:
                return [t for t in nested if isinstance(t, dict)]
    return []


def load_session_system_block(
    session_path: Path,
    counter: TokenCounter,
) -> SessionSystemBlock | None:
    """Parse a session JSONL and return its system block, or None on failure."""
    records = _iter_records(session_path, limit=MAX_RECORDS_SCANNED)
    if not records:
        return None

    system_text = _find_system_text(records)
    tools = _find_tools(records)

    if not system_text and not tools:
        return None

    tools_json = json.dumps(tools, separators=(",", ":")) if tools else ""
    return SessionSystemBlock(
        session_path=session_path,
        system_text=system_text,
        tools_json=tools_json,
        tools=tuple(tools),
        system_tokens=counter.count(system_text),
        tools_tokens=counter.count(tools_json),
    )


def _iter_tool_use_names(record: Any) -> list[str]:
    """Extract every ``tool_use`` block's ``name`` from a session record.

    Claude Code writes assistant turns as ``{"message": {"content": [...]}}``
    where each content block is a dict. We walk that structure and pull
    names from any ``{"type": "tool_use", "name": "..."}`` block. Missing
    keys, non-dict content items, and malformed shapes all simply produce
    an empty list rather than raising.
    """
    if not isinstance(record, dict):
        return []
    message = record.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    names: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_use":
            continue
        name = block.get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return names


def count_mcp_invocations(session_path: Path) -> Mapping[str, int]:
    """Return ``{server_name: invocation_count}`` for one session JSONL.

    We read up to :data:`MAX_INVOCATION_RECORDS` lines and count every
    ``tool_use`` block whose name starts with ``mcp__<server>__``. Each
    block is one invocation regardless of tool name, because from the
    user's perspective "MCP X got used" is the actionable fact.

    Missing / unreadable / malformed JSONLs return an empty mapping.
    """
    counts: dict[str, int] = {}
    try:
        with session_path.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= MAX_INVOCATION_RECORDS:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for name in _iter_tool_use_names(record):
                    if not name.startswith(_MCP_TOOL_PREFIX):
                        continue
                    remainder = name[len(_MCP_TOOL_PREFIX) :]
                    server, _, _ = remainder.partition("__")
                    if server:
                        counts[server] = counts.get(server, 0) + 1
    except OSError:
        return MappingProxyType({})
    return MappingProxyType(counts)
