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

from datetime import UTC, datetime

from unclog.findings.base import Action, Finding, Scope
from unclog.findings.claude_md_context import build_context
from unclog.findings.detectors import (
    claude_md_dead_ref,
    claude_md_duplicate,
    claude_md_oversized,
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
) -> list[Finding]:
    """Run every v0.1 detector and return their findings concatenated.

    Detectors each produce their findings sorted by token savings
    descending within type; the final list preserves the order detectors
    are invoked in (largest-impact categories first: MCPs, plugins,
    CLAUDE.md issues, residue flags).

    v0.1 intentionally does not flag unused agents/commands/skills:
    we cannot reliably distinguish "never used" from "not used lately"
    (Claude dispatches agents via the Task tool without leaving
    @-mention fingerprints the way slash-commands do). Instead, the
    composition block shows the total token cost per category and
    lets the user decide.
    """
    reference = now if now is not None else datetime.now(tz=UTC)
    context = build_context(state)
    findings: list[Finding] = []
    findings.extend(dead_mcp.detect(state, activity, thresholds, now=reference))
    findings.extend(unused_mcp.detect(state, activity, thresholds, now=reference))
    findings.extend(failed_mcp_probe.detect(state, activity, thresholds, now=reference))
    findings.extend(stale_plugin.detect(state, activity, thresholds, now=reference))
    findings.extend(
        claude_md_duplicate.detect(
            state, activity, thresholds, now=reference, context=context
        )
    )
    findings.extend(
        scope_mismatch.detect(state, activity, thresholds, now=reference, context=context)
    )
    findings.extend(
        claude_md_oversized.detect(
            state, activity, thresholds, now=reference, context=context
        )
    )
    findings.extend(
        claude_md_dead_ref.detect(
            state, activity, thresholds, now=reference, context=context
        )
    )
    findings.extend(disabled_plugin_residue.detect(state, activity, thresholds, now=reference))
    findings.extend(missing_claudeignore.detect(state, activity, thresholds, now=reference))
    findings.extend(heavy_hook.detect(state, activity, thresholds, now=reference))
    return findings
