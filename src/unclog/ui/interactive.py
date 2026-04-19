"""Interactive fix flow: prompt → select → apply → summary.

Single-picker flow:

1. Print the scan report, then immediately open one Rich Live sectioned
   multiselect picker. Detector-driven fixes appear in an ``Apply``
   section; opt-in inventory items (agents, skills, remote MCPs) appear
   in ``Curate …`` sections below. The picker is the decision surface —
   no pre-prompt is needed and an empty selection exits without mutating.
2. After selection, confirm with ``Apply N change(s)? [y/N]`` (default No).
3. On accept, create a snapshot and run :mod:`unclog.apply.runner`,
   then render the result and a single cumulative countdown.

Safety defaults (spec §3.2):

- The apply confirm defaults to No — mashing enter exits cleanly.
- Apply-section rows preselect from detector ``auto_checked``; curate
  rows always start unchecked (consent is per-item).
- ``--yes`` skips the picker and applies every auto-checked detector
  finding. Curate items are *never* auto-applied — consent is always
  per-item even in yes-mode.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rich.console import Console, Group, RenderableType
from rich.table import Table
from rich.text import Text

from unclog.apply.runner import ApplyResult, apply_findings
from unclog.findings.base import Finding
from unclog.ui.chrome import rounded_panel, status_glyph
from unclog.ui.countdown import animate_countdown
from unclog.ui.picker import Section, run_rich_multiselect
from unclog.ui.share import render_share_stat
from unclog.ui.theme import ACCENT, DIM, SEVERITY_BAD, SEVERITY_OK
from unclog.util.paths import ClaudePaths

# Same connector used in the scan report to anchor nested child rows
# to their parent headline.
_CONNECTOR = "⎿"


class Prompter(Protocol):
    """Pluggable I/O so tests can drive the flow without a real TTY."""

    def confirm(self, message: str, default: bool) -> bool: ...

    def multiselect_sections(
        self,
        title: str,
        sections: list[Section],
    ) -> list[Finding]: ...


class RichPrompter:
    """Default prompter backed by :mod:`unclog.ui.picker`.

    The picker is a Rich ``Live`` repaint loop driven by ``readchar``.
    Compared to the curses-backed ``pick`` library or prompt_toolkit's
    CPR-based loop, this gives us truecolor category badges, a live
    running-total footer, and reliable input handling across every
    terminal emulator we've tested.
    """

    def __init__(self, console: Console) -> None:
        self._console = console

    def confirm(self, message: str, default: bool) -> bool:
        suffix = " [Y/n] " if default else " [y/N] "
        try:
            answer = input(message + suffix).strip().lower()
        except EOFError:
            # Pipe closed / no TTY — treat as silent "No" so non-interactive
            # contexts never auto-apply. KeyboardInterrupt is deliberately
            # *not* caught here: the top-level CLI wants Ctrl+C to abort
            # the whole run cleanly rather than degrade into a "No" answer
            # the user didn't pick.
            return False
        if not answer:
            return default
        return answer.startswith("y")

    def multiselect_sections(
        self,
        title: str,
        sections: list[Section],
    ) -> list[Finding]:
        if not any(section.findings for section in sections):
            return []
        return run_rich_multiselect(
            sections,
            title=title,
            console=self._console,
        )


@dataclass
class InteractiveOptions:
    """Runtime knobs for :func:`run_interactive`.

    - ``yes``: no prompts; apply every auto-checked finding.
    - ``no_animation``: reserved for M6 polish; accepted now so CLI
      plumbing stabilises.
    """

    yes: bool = False
    no_animation: bool = False


def run_interactive(
    findings: list[Finding],
    *,
    claude_home: Path,
    project_paths: tuple[Path, ...],
    console: Console,
    options: InteractiveOptions,
    prompter: Prompter | None = None,
    baseline_tokens: int | None = None,
    curate_findings: list[Finding] | None = None,
) -> ApplyResult | None:
    """Run the interactive fix flow. Returns the apply result, or None.

    One picker, multiple sections. Detector-driven applicable findings
    fill the ``Apply`` section; opt-in inventory items fill ``Curate
    agents`` / ``Curate skills`` / ``Curate MCPs`` sections. Empty
    sections are dropped. When only one section ends up populated, its
    title is suppressed so the picker mirrors the historical flat list.

    Flag-only findings render a separate manual-hints panel above the
    picker — they're informational next steps that don't fit the
    select-and-apply model.

    ``None`` means no mutations happened.
    """
    curate_findings = curate_findings or []
    if not findings and not curate_findings:
        return None

    applicable = [f for f in findings if f.action.primitive != "flag_only"]
    flag_only = [f for f in findings if f.action.primitive == "flag_only"]

    # --yes path: apply every auto-checked applicable finding
    # non-interactively. Curate is never auto-applied — consent is
    # always per-item.
    if options.yes:
        if not applicable:
            return None
        auto = [f for f in applicable if f.auto_checked]
        if not auto:
            console.print("[dim]--yes: no auto-checked findings to apply.[/dim]")
            return None
        return _execute(
            auto,
            claude_home=claude_home,
            project_paths=project_paths,
            console=console,
            animate=not options.no_animation,
            baseline_tokens=baseline_tokens,
        )

    if prompter is None:
        if not _stdin_is_tty():
            # No interactive input available and --yes wasn't set. Silently
            # skip the fix flow; the report already printed.
            return None
        active_prompter: Prompter = RichPrompter(console)
    else:
        active_prompter = prompter

    # Render flag-only hints whenever they're the only thing in the
    # findings bucket — they're independent of the picker and easy to
    # miss otherwise.
    if not applicable and flag_only:
        _render_informational_next_steps(flag_only, console)

    sections = _build_picker_sections(applicable, curate_findings)
    if not sections:
        return None

    selected = active_prompter.multiselect_sections(
        "Select fixes and curate",
        sections,
    )
    if not selected:
        console.print("[dim]Nothing selected — exiting without changes.[/dim]")
        return None

    if not active_prompter.confirm(
        f"Apply {len(selected)} change(s)?", default=False
    ):
        return None

    return _execute(
        selected,
        claude_home=claude_home,
        project_paths=project_paths,
        console=console,
        animate=not options.no_animation,
        baseline_tokens=baseline_tokens,
    )


def _build_picker_sections(
    applicable: list[Finding],
    curate_findings: list[Finding],
) -> list[Section]:
    """Group findings into picker sections.

    Apply section sorts by token weight desc so the biggest wins are at
    the top regardless of check state. Curate sub-sections preserve
    their incoming order — ``build_curate_findings`` already returns
    them sorted by token desc, so a stable per-type partition keeps that
    order intact.

    Sections with no findings are dropped. When only one section
    survives, its title is blanked so the picker has no header row —
    matches the historical look for callers that only have one bucket.
    """
    sorted_applicable = sorted(
        applicable,
        key=lambda f: (f.token_savings is None, -(f.token_savings or 0), f.title),
    )
    agents = [f for f in curate_findings if f.type == "agent_inventory"]
    skills = [f for f in curate_findings if f.type == "skill_inventory"]
    mcps = [f for f in curate_findings if f.type == "unmeasured_mcp"]

    groups: list[tuple[str, list[Finding], set[int]]] = []
    if sorted_applicable:
        preselected = {i for i, f in enumerate(sorted_applicable) if f.auto_checked}
        groups.append(("Apply", sorted_applicable, preselected))
    if agents:
        groups.append(("Curate agents", agents, set()))
    if skills:
        groups.append(("Curate skills", skills, set()))
    if mcps:
        groups.append(("Curate MCPs", mcps, set()))

    if not groups:
        return []

    suppress_title = len(groups) == 1
    return [
        Section(
            title="" if suppress_title else title,
            findings=findings,
            preselected=preselected,
        )
        for title, findings, preselected in groups
    ]


def _execute(
    findings: list[Finding],
    *,
    claude_home: Path,
    project_paths: tuple[Path, ...],
    console: Console,
    animate: bool,
    baseline_tokens: int | None,
) -> ApplyResult | None:
    """Apply ``findings``, render the result, run the countdown."""
    paths = ClaudePaths(home=claude_home)
    result = apply_findings(
        findings,
        claude_home=claude_home,
        snapshots_dir=paths.snapshots_dir,
        project_paths=project_paths,
    )
    _render_result(result, console)
    if baseline_tokens is not None and result.token_savings:
        console.print("")
        animate_countdown(
            console,
            before=baseline_tokens,
            after=baseline_tokens - result.token_savings,
            animate=animate,
        )
        render_share_stat(
            console,
            baseline_tokens=baseline_tokens,
            tokens_saved=result.token_savings,
        )
    _maybe_warn_retention(paths, console)
    return result


def _render_result(result: ApplyResult, console: Console) -> None:
    """Render the post-apply panel in the Claude-Code visual vocabulary.

    Panel title carries a status glyph — ``⏺ Applied`` on success,
    ``! Applied with failures`` when any action failed. The headline
    row (``N changes applied · ~X,XXX tokens saved``) sits at the top
    of the body; per-action rows use ``⎿ ✓`` connectors; snapshot +
    undo sit in a two-row label grid mirroring the welcome panel.
    Border colour is the one place in the product where colour encodes
    semantics (success vs failure) — kept.
    """
    console.print("")
    blocks: list[RenderableType] = []

    summary = Text()
    summary.append(f"{len(result.succeeded)}", style=f"bold {SEVERITY_OK}")
    summary.append(" changes applied", style=DIM)
    if result.token_savings:
        summary.append("  ·  ", style=DIM)
        summary.append(f"~{result.token_savings:,}", style=f"bold {SEVERITY_OK}")
        summary.append(" tokens saved", style=DIM)
    blocks.append(summary)

    if result.succeeded:
        blocks.append(Text(""))
        for finding, _ in result.succeeded:
            row = Text()
            row.append(f"  {_CONNECTOR} ", style=DIM)
            row.append("✓ ", style=SEVERITY_OK)
            savings = finding.token_savings
            if savings is not None:
                row.append(f"{savings:>6,} tok  ", style=DIM)
            else:
                row.append("     — tok  ", style=DIM)
            row.append(finding.title, style="default")
            blocks.append(row)

    if result.failed:
        blocks.append(Text(""))
        fail_header = Text()
        fail_header.append("! ", style="bold #eab308")
        fail_header.append(f"{len(result.failed)} action(s) failed", style=SEVERITY_BAD)
        blocks.append(fail_header)
        for finding, reason in result.failed:
            row = Text()
            row.append(f"  {_CONNECTOR} ", style=DIM)
            row.append("✗ ", style=SEVERITY_BAD)
            row.append(finding.title, style="default")
            row.append(f"  — {reason}", style=DIM)
            blocks.append(row)

    # Persist error is rare but data-loss-by-surprise if hidden: a
    # manifest that didn't land means ``unclog restore <id>`` can't find
    # the snapshot. Tell the user immediately so they don't rely on undo.
    if result.persist_error:
        blocks.append(Text(""))
        warn = Text()
        warn.append("! ", style="bold #eab308")
        warn.append("snapshot manifest failed to persist", style=SEVERITY_BAD)
        warn.append(
            f"  — {result.persist_error}. Undo for this batch may not work.",
            style=DIM,
        )
        blocks.append(warn)

    blocks.append(Text(""))
    meta = Table.grid(padding=(0, 2))
    meta.add_column(style=DIM, no_wrap=True)
    meta.add_column(no_wrap=False)
    meta.add_row("snapshot", Text(str(result.snapshot.root), style="default"))
    meta.add_row(
        "undo",
        Text(f"unclog restore {result.snapshot.id}", style=f"bold {ACCENT}"),
    )
    blocks.append(meta)

    # Title: glyph carries the headline. ⏺ on clean success, ! on any
    # degradation (action failure or manifest-persist failure). Border
    # colour still encodes state for at-a-glance scan.
    degraded = bool(result.failed) or bool(result.persist_error)
    if degraded:
        title = Text()
        title.append_text(status_glyph("attention"))
        label = "Applied with failures" if result.failed else "Applied (undo at risk)"
        title.append(label, style=f"bold {ACCENT}")
        border = SEVERITY_BAD
    else:
        title = Text()
        title.append_text(status_glyph("running"))
        title.append("Applied", style=f"bold {ACCENT}")
        border = SEVERITY_OK

    console.print(rounded_panel(Group(*blocks), title=title, border=border))


def _render_informational_next_steps(
    findings: list[Finding], console: Console
) -> None:
    """Print a manual-remediation hint block when nothing is auto-applicable.

    Flag-only findings are surfaced by detectors that can identify a
    problem but intentionally decline to fix it automatically (missing
    ``.claudeignore``, recently-disabled plugin residue, etc. — spec §6).
    Rather than exiting silently, we give the user a concrete next step
    per finding type so they know what to do.

    Rendered in the same rounded-panel vocabulary as the applied panel:
    ``!`` amber glyph on the title, ``⎿`` connectors for each row.
    """
    console.print("")
    blocks: list[RenderableType] = []

    header = Text()
    header.append("No auto-fixable issues.", style="bold")
    header.append(
        f"  {len(findings)} informational finding(s) — handle manually:",
        style=DIM,
    )
    blocks.append(header)
    blocks.append(Text(""))

    seen_hints: set[str] = set()
    for f in findings:
        hint = _manual_hint_for(f)
        key = f"{f.type}:{hint}"
        if key in seen_hints:
            continue
        seen_hints.add(key)
        row = Text()
        row.append(f"  {_CONNECTOR} ", style=DIM)
        row.append(f.title, style="default")
        row.append(f"  → {hint}", style=DIM)
        blocks.append(row)

    blocks.append(Text(""))
    footer = Text()
    footer.append("Run ", style=DIM)
    footer.append("unclog --json", style=f"bold {ACCENT}")
    footer.append(" for full evidence on each finding.", style=DIM)
    blocks.append(footer)

    title = Text()
    title.append_text(status_glyph("attention"))
    title.append("Manual next steps", style=f"bold {ACCENT}")
    console.print(rounded_panel(Group(*blocks), title=title))


def _manual_hint_for(finding: Finding) -> str:
    """Human next-step for a flag-only finding."""
    path = finding.action.path
    plugin_key = finding.action.plugin_key
    match finding.type:
        case "missing_claudeignore":
            target = str(path) if path else ".claudeignore"
            return f"create {target} with node_modules/ .venv/ etc."
        case "disabled_plugin_residue":
            key = plugin_key or finding.id.split(":", 1)[-1]
            return (
                f"leave in place; unclog will offer removal once "
                f"{key!r} is long-disabled"
            )
        case "claude_md_dead_ref":
            target = str(path) if path else "the referenced CLAUDE.md"
            return f"review and rewrite surrounding prose in {target}"
        case "failed_mcp_probe":
            server = finding.action.server_name or "the server"
            return (
                f"check that {server!r}'s command is on PATH and starts "
                f"cleanly; see --json evidence for captured stderr"
            )
        case _:
            return "see --json output for evidence"


_RETENTION_WARN_THRESHOLD = 20


def _maybe_warn_retention(paths: ClaudePaths, console: Console) -> None:
    if not paths.snapshots_dir.is_dir():
        return
    count = sum(1 for child in paths.snapshots_dir.iterdir() if child.is_dir())
    if count > _RETENTION_WARN_THRESHOLD:
        console.print(
            f"[#eab308]![/#eab308] [dim]{count} snapshots stored at "
            f"{paths.snapshots_dir} — consider pruning (coming in v0.2).[/dim]"
        )


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


__all__ = [
    "InteractiveOptions",
    "Prompter",
    "RichPrompter",
    "run_interactive",
]
