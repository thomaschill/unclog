"""Welcome panel — the product frame rendered at the top of every run.

Replaces the old one-line ``▁▂▃ unclog`` wordmark with a rounded
thin-border panel containing the version, a one-line description of
what unclog does, and (in verbose mode) live scan metadata + a short
tips list.

Two layouts:

- **Default**: title + tagline only. Smaller hero, more room for the
  baseline panel below it. The first time a user runs unclog the
  caller should also print :func:`first_run_tip_line` to surface the
  safety message once before disappearing forever.
- **Verbose** (``--verbose``): the historical layout — tagline, scan
  metadata grid, and the full tips list inside the panel.

Deliberately thin on behaviour: accepts an :class:`InstallationState`
and derives everything it needs. The caller decides where to print it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from unclog import __version__
from unclog.state import InstallationState
from unclog.ui.chrome import rounded_panel
from unclog.ui.theme import ACCENT, DIM
from unclog.util.paths import ClaudePaths

# Short list chosen to surface the non-obvious safety properties and
# the escape hatch for automation. Three items is the limit — more
# pushes the hero out of the first screen on a laptop terminal.
_TIPS: tuple[str, ...] = (
    "nothing is mutated until you confirm",
    "every apply snapshots for undo",
    "--json for machine-readable output",
)

# The sentinel that flips first-run messaging off forever. Empty file —
# its presence is the only signal we need. Lives under ``.unclog/`` so
# it tracks with the snapshot dir if the user moves their Claude home.
_FIRST_RUN_MARKER_NAME = "first-run-seen"

# Single-line first-run reassurance. Compresses two of the three tips
# into one dim hint that sits below the panel for one run only — the
# safety story without the persistent footprint.
_FIRST_RUN_TIP = (
    "first run? nothing is mutated until you confirm  ·  "
    "every apply snapshots for undo  ·  --verbose for more context"
)


def welcome_panel(state: InstallationState, *, verbose: bool = False) -> RenderableType:
    """Return the rounded welcome panel for ``state``.

    Default mode renders just the title + tagline (with the version in
    the subtitle). ``verbose=True`` restores the historical layout with
    the scan-meta grid and the full tips list.

    Scanning metadata (verbose only) reflects what the scan actually
    looked at:

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

    if not verbose:
        return rounded_panel(tagline, title=title, subtitle=version_subtitle)

    meta = _scan_meta(state)
    tips = _tips_block()
    body = Group(tagline, Text(""), meta, Text(""), tips)
    return rounded_panel(body, title=title, subtitle=version_subtitle)


def first_run_tip_line() -> Text:
    """Return the one-line first-run reassurance hint.

    Rendered below the panel by the caller when this is the user's
    first invocation. Single line, dim styling — visible enough to
    register on first read, quiet enough to not feel like chrome.
    """
    return Text(_FIRST_RUN_TIP, style=DIM)


def is_first_run(paths: ClaudePaths) -> bool:
    """Has the first-run marker been written under ``paths``?

    Defaults to "yes, this is the first run" if the marker can't be
    read for any reason — false-positive (showing the tip twice) is
    less bad than false-negative (silently swallowing the safety
    message on the very first run).
    """
    try:
        return not (paths.unclog_dir / _FIRST_RUN_MARKER_NAME).exists()
    except OSError:
        return True


def mark_first_run_seen(paths: ClaudePaths) -> None:
    """Drop the empty sentinel file that flips first-run messaging off.

    Best-effort: a permissions failure on the unclog dir downgrades to
    "the user sees the tip again next time", which is harmless. We do
    not surface the error — it would be noise on a path that's already
    succeeded at the work the user actually asked for.
    """
    try:
        paths.unclog_dir.mkdir(parents=True, exist_ok=True)
        (paths.unclog_dir / _FIRST_RUN_MARKER_NAME).touch(exist_ok=True)
    except OSError:
        pass


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
    return datetime.fromtimestamp(mtime, tz=UTC).strftime("%Y-%m-%d")


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


__all__ = [
    "first_run_tip_line",
    "is_first_run",
    "mark_first_run_seen",
    "welcome_panel",
]
