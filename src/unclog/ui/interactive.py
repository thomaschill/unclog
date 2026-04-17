"""Interactive fix flow: prompt → select → apply → summary.

Two-phase flow per spec §3.1:

1. After the scan report is printed, prompt ``Fix these? [y/N]``. The
   default is No so mashing enter exits cleanly.
2. If the user accepts, show a questionary checkbox list. Findings
   whose ``auto_checked`` bit is ``True`` are pre-ticked; everything
   else starts unchecked. The user toggles selections, hits enter.
3. A second prompt ``Apply N changes? [y/N]`` confirms the shortlist.
4. If accepted, create a snapshot and run :mod:`unclog.apply.runner`.
   The result is rendered with file paths and token savings.

Safety defaults (spec §3.2):

- Both Y/N prompts default to No.
- Dead-MCP and "broken" findings are never pre-ticked even when the
  detector's auto_checked says so (``dead_mcp`` comes back false from
  the detector already; this is a belt-and-braces filter).
- ``--dry-run`` short-circuits right before apply: the user sees the
  plan, no snapshot is created, no files change.
- ``--yes`` skips both prompts and applies every auto-checked finding.
  Opt-in findings are silently *excluded* — consent is still required
  for them even in yes-mode.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import questionary
from rich.console import Console

from unclog.apply.runner import ApplyResult, apply_findings
from unclog.findings.base import Finding
from unclog.ui.countdown import animate_countdown
from unclog.ui.share import render_share_stat
from unclog.util.paths import ClaudePaths


class Prompter(Protocol):
    """Pluggable I/O so tests can drive the flow without a real TTY."""

    def confirm(self, message: str, default: bool) -> bool: ...

    def multiselect(
        self, message: str, choices: list[tuple[str, Finding]], defaults: set[str]
    ) -> list[Finding]: ...


class QuestionaryPrompter:
    """Default prompter backed by the ``questionary`` library."""

    def confirm(self, message: str, default: bool) -> bool:
        answer = questionary.confirm(message, default=default).ask()
        # ``ask()`` returns None on Ctrl-C / stream close — treat as No.
        return bool(answer) if answer is not None else False

    def multiselect(
        self, message: str, choices: list[tuple[str, Finding]], defaults: set[str]
    ) -> list[Finding]:
        finding_by_title = {title: finding for title, finding in choices}
        q_choices = [
            questionary.Choice(title=title, checked=(title in defaults))
            for title, _ in choices
        ]
        picked = questionary.checkbox(message, choices=q_choices).ask()
        if picked is None:
            return []
        return [finding_by_title[title] for title in picked if title in finding_by_title]


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
        console.print("[dim]All findings are informational — nothing to apply.[/dim]")
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
        active_prompter: Prompter = QuestionaryPrompter()
    else:
        active_prompter = prompter

    if not active_prompter.confirm("Fix these?", default=False):
        return None

    choices = [(_format_choice(f), f) for f in applicable]
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
    console.print(f"[#22c55e]\u2713[/#22c55e] Snapshot  [dim]{result.snapshot.root}[/dim]")
    console.print(
        f"[#22c55e]\u2713[/#22c55e] Applied {len(result.succeeded)} change(s)"
    )
    if result.token_savings:
        console.print(
            f"  [dim]Saved ~{result.token_savings:,} tokens.[/dim]"
        )
    if result.failed:
        console.print("")
        console.print(f"[#ef4444]! {len(result.failed)} action(s) failed:[/#ef4444]")
        for finding, reason in result.failed:
            console.print(f"  [dim]- {finding.title}: {reason}[/dim]")
    console.print("")
    console.print(f"[dim]Undo:  unclog restore {result.snapshot.id}[/dim]")


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
    savings = (
        f"{finding.token_savings:>6,} tok"
        if finding.token_savings is not None
        else "       —"
    )
    scope_kind = finding.scope.kind
    return f"[{scope_kind}] {finding.title}  ·  {savings}"


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


__all__ = [
    "InteractiveOptions",
    "Prompter",
    "QuestionaryPrompter",
    "run_interactive",
]
