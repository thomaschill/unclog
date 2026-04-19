"""Finding detectors.

A *finding* is a typed, immutable record that says "here is something in
your Claude Code installation that could be cleaned up." Detectors are
pure functions of the :class:`~unclog.state.InstallationState`; they
never touch the filesystem. Applying a fix (M5) is a separate phase.

Public surface:

- :class:`~unclog.findings.base.Finding`, :class:`~unclog.findings.base.Action`,
  :class:`~unclog.findings.base.Scope`
- :class:`~unclog.findings.thresholds.Thresholds` and
  :func:`~unclog.findings.thresholds.load_thresholds`
- :func:`detect` — top-level entry point that runs every v0.1 detector.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from unclog.findings.base import Action, Finding, Scope
from unclog.findings.claude_md_context import build_context
from unclog.findings.detectors import (
    claude_md_duplicate,
    dead_mcp,
    disabled_plugin_residue,
    failed_mcp_probe,
    heavy_hook,
    missing_claudeignore,
    scope_mismatch,
    stale_plugin,
    unused_mcp,
)
from unclog.findings.thresholds import Thresholds, load_thresholds
from unclog.scan.stats import ActivityIndex
from unclog.state import InstallationState

__all__ = [
    "Action",
    "ActivityIndex",
    "Finding",
    "Scope",
    "Thresholds",
    "detect",
    "load_thresholds",
]


def detect(
    state: InstallationState,
    activity: ActivityIndex,
    thresholds: Thresholds,
    *,
    now: datetime | None = None,
    warnings: list[str] | None = None,
) -> list[Finding]:
    """Run every v0.1 detector and return their findings concatenated.

    Detectors each produce their findings sorted by token savings
    descending within type; the final list preserves the order detectors
    are invoked in (largest-impact categories first: MCPs, plugins,
    CLAUDE.md issues, residue flags).

    Each detector runs inside its own ``try``/``except`` — a bug in one
    detector skips *that* category and reports it via ``warnings`` (when
    provided), rather than silently returning an empty finding list and
    dumping a traceback to stderr. This is the "never crash the whole
    audit because one detector hit a malformed input" guarantee.

    v0.1 intentionally does not flag unused agents/commands/skills:
    we cannot reliably distinguish "never used" from "not used lately"
    (Claude dispatches agents via the Task tool without leaving
    @-mention fingerprints the way slash-commands do). Instead, the
    composition block shows the total token cost per category and
    lets the user decide.
    """
    reference = now if now is not None else datetime.now(tz=UTC)
    try:
        context = build_context(state)
    except Exception as exc:
        # build_context failure disables every CLAUDE.md-based detector
        # but the MCP / plugin / hook ones can still run — so we don't
        # abort here.
        if warnings is not None:
            warnings.append(f"could not parse CLAUDE.md files: {exc}")
        context = None

    findings: list[Finding] = []

    def _run(name: str, fn: Callable[[], list[Finding]]) -> None:
        try:
            findings.extend(fn())
        except Exception as exc:
            if warnings is not None:
                warnings.append(f"{name} detector skipped: {exc}")

    _run("dead_mcp", lambda: dead_mcp.detect(state, activity, thresholds, now=reference))
    _run("unused_mcp", lambda: unused_mcp.detect(state, activity, thresholds, now=reference))
    _run(
        "failed_mcp_probe",
        lambda: failed_mcp_probe.detect(state, activity, thresholds, now=reference),
    )
    _run(
        "stale_plugin",
        lambda: stale_plugin.detect(state, activity, thresholds, now=reference),
    )
    if context is not None:
        _run(
            "claude_md_duplicate",
            lambda: claude_md_duplicate.detect(
                state, activity, thresholds, now=reference, context=context
            ),
        )
        _run(
            "scope_mismatch",
            lambda: scope_mismatch.detect(
                state, activity, thresholds, now=reference, context=context
            ),
        )
    _run(
        "disabled_plugin_residue",
        lambda: disabled_plugin_residue.detect(state, activity, thresholds, now=reference),
    )
    _run(
        "missing_claudeignore",
        lambda: missing_claudeignore.detect(state, activity, thresholds, now=reference),
    )
    _run(
        "heavy_hook",
        lambda: heavy_hook.detect(state, activity, thresholds, now=reference),
    )
    return findings
