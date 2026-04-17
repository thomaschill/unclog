"""Detect CLAUDE.md sections large enough that they deserve editor review.

A single section crossing the 1,000-token line is almost always either
(a) a rule block that has outgrown its heading and should become a
skill, or (b) an un-pruned brain-dump. Either way, v0.1 cannot make the
call automatically — the finding is ``open_in_editor`` with a line
hint, and never auto-checked (spec §6).

Threshold is intentionally non-configurable in v0.1 per spec §20
decision 4; baseline thresholds stay stable across users so screenshots
and screenshots-from-screenshots mean the same thing.
"""

from __future__ import annotations

from datetime import datetime

from unclog.findings.base import Action, Finding, Scope
from unclog.findings.claude_md_context import ClaudeMdContext, ScopedClaudeMd
from unclog.findings.thresholds import Thresholds
from unclog.scan.claude_md import MeasuredSection
from unclog.scan.stats import ActivityIndex
from unclog.state import InstallationState

OVERSIZED_SECTION_TOKENS = 1_000


def detect(
    state: InstallationState,
    activity: ActivityIndex,
    thresholds: Thresholds,
    *,
    now: datetime,
    context: ClaudeMdContext,
) -> list[Finding]:
    findings: list[Finding] = []
    for scoped in context.files:
        for measured in scoped.parsed.sections:
            if measured.tokens < OVERSIZED_SECTION_TOKENS:
                continue
            findings.append(_build(scoped, measured))
    findings.sort(key=lambda f: f.token_savings or 0, reverse=True)
    return findings


def _build(scoped: ScopedClaudeMd, measured: MeasuredSection) -> Finding:
    section = measured.section
    heading = section.heading_text or "<preamble>"
    # Build a stable id that survives edits to surrounding sections.
    id_key = f"{scoped.parsed.path}:{section.start_line}:{heading}"
    scope = Scope(
        kind="global" if scoped.scope_kind == "global" else "project",
        project_path=scoped.project_path,
    )
    return Finding(
        id=f"claude_md_oversized:{id_key}",
        type="claude_md_oversized",
        title=f"Review oversized CLAUDE.md section: {heading!r}",
        reason=(
            f"{measured.tokens:,} tokens in one section "
            f"(>{OVERSIZED_SECTION_TOKENS:,} threshold)"
        ),
        scope=scope,
        action=Action(
            primitive="open_in_editor",
            path=scoped.parsed.path,
            heading=heading,
            line_numbers=(section.start_line,),
        ),
        auto_checked=False,
        token_savings=None,
        evidence={
            "path": str(scoped.parsed.path),
            "heading": heading,
            "heading_path": list(section.heading_path),
            "tokens": measured.tokens,
            "start_line": section.start_line,
            "end_line": section.end_line,
            "variant": scoped.variant,
        },
    )
