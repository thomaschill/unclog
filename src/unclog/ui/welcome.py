"""Welcome panel — the product frame rendered at the top of every run.

Replaces the old one-line ``▁▂▃ unclog`` wordmark with a rounded
thin-border panel containing the version, a one-line description of
what unclog does, live scan metadata (home, project count, session
freshness) and a short tips list.

Deliberately thin on behaviour: accepts an :class:`InstallationState`
and derives everything it needs. The caller decides where to print it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from unclog import __version__
from unclog.state import InstallationState
from unclog.ui.chrome import rounded_panel
from unclog.ui.theme import ACCENT, DIM


# Short list chosen to surface the non-obvious safety properties and
# the escape hatch for automation. Three items is the limit — more
# pushes the hero out of the first screen on a laptop terminal.
_TIPS: tuple[str, ...] = (
    "nothing is mutated until you confirm",
    "every apply snapshots for undo",
    "--json for machine-readable output",
)


def welcome_panel(state: InstallationState) -> RenderableType:
    """Return the rounded welcome panel for ``state``.

    Scanning metadata reflects what the scan actually looked at:
    - ``scanning`` — the ``claude_home`` path the scan walked.
    - ``projects`` — number of registered projects in ``~/.claude.json``.
    - ``session`` — most recent session timestamp + source project, or
      ``no sessions found`` when no JSONL existed anywhere.
    """
    # Title stays short and accent-clean; version migrates to the
    # bottom-right subtitle so the top border reads like a product
    # nameplate rather than a string of metadata.
    title = Text("unclog", style=f"bold {ACCENT}")
    version_subtitle = Text(f"v{__version__}", style=DIM)

    tagline = Text("local-only audit of your Claude Code installation", style=DIM)

    meta = _scan_meta(state)
    tips = _tips_block()

    body = Group(tagline, Text(""), meta, Text(""), tips)
    return rounded_panel(body, title=title, subtitle=version_subtitle)


def _scan_meta(state: InstallationState) -> Table:
    """Three-row `label  value` table describing what was scanned.

    Uses ``Table`` with hidden headers/edges so the two columns align
    cleanly without the caller hard-coding a padding width.
    """
    gs = state.global_scope
    projects_known = len(gs.config.projects) if gs.config else 0

    # Prefer the raw JSONL path over ``latest_session`` here: modern
    # Claude Code sessions don't write a parseable system block, so
    # ``latest_session`` is almost always ``None`` on real installs
    # even when plenty of recent JSONLs exist on disk.
    session_path = gs.latest_session_path
    if session_path is None:
        session_value = "no sessions found"
        session_style = DIM
    else:
        ts = _format_session_mtime(session_path)
        session_value = f"{ts}  ·  latest loaded"
        session_style = "default"

    table = Table.grid(padding=(0, 2))
    table.add_column(style=DIM, no_wrap=True)
    table.add_column(no_wrap=False)
    table.add_row("scanning", Text(str(gs.claude_home), style="default"))
    table.add_row("projects", Text(f"{projects_known} known", style="default"))
    table.add_row("session", Text(session_value, style=session_style))
    return table


def _format_session_mtime(session_path: Path) -> str:
    """Format the session JSONL's mtime as ``YYYY-MM-DD``.

    The file's modification time is a reliable proxy for "how recent is
    this session" — Claude Code rewrites the JSONL on every turn, so
    mtime == last-active time. Defensive against a path that disappears
    between scan and render.
    """
    try:
        mtime = session_path.stat().st_mtime
    except OSError:
        return "unknown"
    return datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d")


def _tips_block() -> Table:
    """``tips:`` label column + one row per tip.

    Kept as a grid so the bullets align under each other regardless of
    terminal width. The label only appears on the first row; subsequent
    rows use blank space so the bullets sit in a single column.
    """
    table = Table.grid(padding=(0, 2))
    table.add_column(style=DIM, no_wrap=True)
    table.add_column(style=DIM, no_wrap=True)
    table.add_column(no_wrap=False)
    for i, tip in enumerate(_TIPS):
        label = "tips:" if i == 0 else ""
        table.add_row(label, Text("•", style=ACCENT), Text(tip, style=DIM))
    return table


__all__ = ["welcome_panel"]
