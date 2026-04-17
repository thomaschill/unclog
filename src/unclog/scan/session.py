"""Parse the first system block from a project's latest session JSONL.

This is unclog's single load-bearing technical read: it is how we learn
the real token footprint of a Claude Code session (CLAUDE.md, skills,
MCP schemas, and everything Claude Code itself injects). We measure
what actually hit the wire rather than what *could* hit the wire.

The Claude Code session JSONL format is not publicly documented and is
expected to drift across releases. This parser is intentionally
permissive: it scans a bounded prefix of the file, skips malformed
lines, accepts several plausible shapes for the system prompt, and
returns ``None`` when it cannot find anything it recognises. Callers
degrade to byte-estimate baselines rather than crashing.

Scope for M2: extract the system prompt text and any ``tools`` array,
and count tokens for each. Per-source attribution (which tokens came
from which MCP / skill / CLAUDE.md) is layered on top of this in
``unclog.app`` where we have the filesystem state to compare against.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from unclog.scan.tokens import TokenCounter

# Cap how far into a JSONL we look for the system block. The system
# prompt lands on the first user turn, so this is generous.
MAX_RECORDS_SCANNED = 25


@dataclass(frozen=True)
class SessionSystemBlock:
    """What Claude Code actually injected at the start of a session."""

    session_path: Path
    system_text: str
    tools_json: str
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
        system_tokens=counter.count(system_text),
        tools_tokens=counter.count(tools_json),
    )
