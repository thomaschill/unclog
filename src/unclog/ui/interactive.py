"""Interactive fix flow: prompt → select → apply → summary.

Two-phase flow per spec §3.1:

1. Print the scan report, then immediately open a Rich Live multiselect
   picker. The picker is the decision surface — no pre-prompt is needed
   and an empty selection exits without mutating anything.
2. After selection, confirm with ``Apply N changes? [y/N]`` (default No).
3. On accept, create a snapshot and run :mod:`unclog.apply.runner`.
   The result is rendered with file paths and token savings.

Safety defaults (spec §3.2):

- The apply confirm defaults to No — mashing enter exits cleanly.
- Findings start unchecked regardless of detector ``auto_checked``;
  the bulk ``A``/``a``/``n`` keybinds cover the sweep case.
- ``--dry-run`` short-circuits right before apply: the user sees the
  plan, no snapshot is created, no files change.
- ``--yes`` skips the picker and applies every auto-checked finding.
  Opt-in findings are silently *excluded* — consent is still required
  for them even in yes-mode.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from unclog.apply.runner import ApplyResult, apply_findings
from unclog.findings.base import Finding
from unclog.ui.countdown import animate_countdown
from unclog.ui.picker import run_rich_multiselect
from unclog.ui.share import render_share_stat
from unclog.ui.theme import ACCENT, DIM, SEVERITY_CLOGGED, SEVERITY_LEAN
from unclog.util.paths import ClaudePaths


class Prompter(Protocol):
    """Pluggable I/O so tests can drive the flow without a real TTY."""

    def confirm(self, message: str, default: bool) -> bool: ...

    def multiselect(
        self, message: str, choices: list[tuple[str, Finding]], defaults: set[str]
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
        except (EOFError, KeyboardInterrupt):
            return False
        if not answer:
            return default
        return answer.startswith("y")

    def multiselect(
        self, message: str, choices: list[tuple[str, Finding]], defaults: set[str]
    ) -> list[Finding]:
        if not choices:
            return []
        findings = [finding for _, finding in choices]
        preselected = {
            i for i, (title, _) in enumerate(choices) if title in defaults
        }
        return run_rich_multiselect(
            findings,
            title=message,
            preselected=preselected,
            console=self._console,
        )


@dataclass
class InteractiveOptions:
    """Runtime knobs for :func:`run_interactive`.

    - ``dry_run``: prompts still run, but the apply phase is skipped.
    - ``yes``: no prompts; apply every auto-checked finding.
    - ``no_animation``: reserved for M6 polish; accepted now so CLI
      plumbing stabilises.
    """

    dry_run: bool = False
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
) -> ApplyResult | None:
    """Run the interactive fix flow. Returns the apply result, or None.

    ``None`` means the user exited before apply (said No to either
    prompt, deselected everything, or we're in dry-run mode). Callers
    should treat ``None`` as "no mutations happened."
    """
    if not findings:
        return None

    applicable = [f for f in findings if f.action.primitive != "flag_only"]
    if not applicable:
        _render_informational_next_steps(findings, console)
        return None

    if options.yes:
        auto = [f for f in applicable if f.auto_checked]
        if not auto:
            console.print("[dim]--yes: no auto-checked findings to apply.[/dim]")
            return None
        return _execute(
            auto,
            claude_home=claude_home,
            project_paths=project_paths,
            console=console,
            dry_run=options.dry_run,
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

    # Sort descending by token weight so the biggest wins are at the top
    # of the picker regardless of check state. Entries without a measured
    # savings value sort last.
    sorted_applicable = sorted(
        applicable,
        key=lambda f: (f.token_savings is None, -(f.token_savings or 0), f.title),
    )
    choices = [(_format_choice(f), f) for f in sorted_applicable]
    defaults = {title for title, f in choices if f.auto_checked}
    selected = active_prompter.multiselect(
        "Select fixes to apply:", choices=choices, defaults=defaults
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
        dry_run=options.dry_run,
        animate=not options.no_animation,
        baseline_tokens=baseline_tokens,
    )


def _execute(
    findings: list[Finding],
    *,
    claude_home: Path,
    project_paths: tuple[Path, ...],
    console: Console,
    dry_run: bool,
    animate: bool,
    baseline_tokens: int | None,
) -> ApplyResult | None:
    if dry_run:
        console.print(f"[dim]--dry-run: would apply {len(findings)} change(s).[/dim]")
        for f in findings:
            console.print(f"  [dim]-[/dim] {f.title}")
        return None
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
    console.print("")
    lines: list[Text] = []

    applied = Text()
    applied.append("✓ ", style=f"bold {SEVERITY_LEAN}")
    applied.append(f"Applied {len(result.succeeded)} change(s)", style="default")
    if result.token_savings:
        applied.append("   ·   ", style=DIM)
        applied.append(f"~{result.token_savings:,}", style=f"bold {SEVERITY_LEAN}")
        applied.append(" tokens saved", style=DIM)
    lines.append(applied)

    snapshot_line = Text()
    snapshot_line.append("  Snapshot  ", style=DIM)
    snapshot_line.append(str(result.snapshot.root), style="default")
    lines.append(snapshot_line)

    if result.failed:
        lines.append(Text(""))
        fail_header = Text()
        fail_header.append("! ", style=f"bold {SEVERITY_CLOGGED}")
        fail_header.append(f"{len(result.failed)} action(s) failed", style=SEVERITY_CLOGGED)
        lines.append(fail_header)
        for finding, reason in result.failed:
            row = Text()
            row.append("  · ", style=DIM)
            row.append(finding.title, style="default")
            row.append(f"  — {reason}", style=DIM)
            lines.append(row)

    lines.append(Text(""))
    undo_line = Text()
    undo_line.append("Undo:  ", style=DIM)
    undo_line.append(f"unclog restore {result.snapshot.id}", style=f"bold {ACCENT}")
    lines.append(undo_line)

    border = SEVERITY_CLOGGED if result.failed else SEVERITY_LEAN
    console.print(
        Panel(
            Text("\n").join(lines),
            title=Text("Applied", style=f"bold {ACCENT}"),
            title_align="left",
            border_style=border,
            padding=(1, 2),
        )
    )


def _render_informational_next_steps(
    findings: list[Finding], console: Console
) -> None:
    """Print a manual-remediation hint block when nothing is auto-applicable.

    Flag-only findings are surfaced by detectors that can identify a
    problem but intentionally decline to fix it automatically (missing
    ``.claudeignore``, recently-disabled plugin residue, etc. — spec §6).
    Rather than exiting silently, we give the user a concrete next step
    per finding type so they know what to do.
    """
    console.print("")
    lines: list[Text] = []

    header = Text()
    header.append("No auto-fixable issues.", style="bold")
    header.append(
        f"   {len(findings)} informational finding(s) — handle manually:",
        style=DIM,
    )
    lines.append(header)
    lines.append(Text(""))

    seen_hints: set[str] = set()
    for f in findings:
        hint = _manual_hint_for(f)
        key = f"{f.type}:{hint}"
        if key in seen_hints:
            continue
        seen_hints.add(key)
        row = Text()
        row.append("  · ", style=DIM)
        row.append(f.title, style="default")
        row.append(f"  → {hint}", style=DIM)
        lines.append(row)

    lines.append(Text(""))
    footer = Text()
    footer.append("Run ", style=DIM)
    footer.append("unclog --json", style=f"bold {ACCENT}")
    footer.append(" for full evidence on each finding.", style=DIM)
    lines.append(footer)

    console.print(
        Panel(
            Text("\n").join(lines),
            title=Text("Manual next steps", style=f"bold {ACCENT}"),
            title_align="left",
            border_style=DIM,
            padding=(1, 2),
        )
    )


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


def _format_choice(finding: Finding) -> str:
    """Format one row of the multiselect picker.

    Token count is positioned on the LEFT (right-padded to a fixed width)
    so any terminal-width truncation never drops the most
    important piece of information. Example line:

        4,192 tok  [global] Remove agent Frontend Developer
    """
    savings = (
        f"{finding.token_savings:>6,} tok"
        if finding.token_savings is not None
        else "     — tok"
    )
    scope_kind = finding.scope.kind
    return f"{savings}  [{scope_kind}] {finding.title}"


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
