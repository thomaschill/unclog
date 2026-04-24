"""Locate the most recent session JSONL for a project.

Session JSONLs are the only source of attribution for MCP tokens in 0.2
— every other signal (system prompt text, tools array layout, invocation
counts) that older revisions tried to extract has either been removed
from Claude Code's on-disk format or is unused by the current product.
Keep this module small: find the latest file, let callers parse it.
"""

from __future__ import annotations

from pathlib import Path


def latest_session_path(project_session_dir: Path) -> Path | None:
    """Return the most recently modified ``*.jsonl`` in the directory, or None."""
    if not project_session_dir.is_dir():
        return None
    candidates = [p for p in project_session_dir.iterdir() if p.suffix == ".jsonl" and p.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)
