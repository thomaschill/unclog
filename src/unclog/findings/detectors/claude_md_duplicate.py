"""Exact-duplicate section detection across global + project CLAUDE.md.

If the same body hash appears in the global CLAUDE.md and in at least
one project CLAUDE.md, the global copy is redundant — the project copy
already loads it when that project is open, and the global copy costs
context in every other project too. The auto-check action strips the
global section; the project version stays untouched.

Spec §6 calls this auto-check = yes. The action is deterministic (we
know the exact section to remove and have its hash for verification
in the snapshot manifest).

v0.1 is exact-body only; near-duplicate and fuzzy-match detection is
deferred to v0.2.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from unclog.findings.base import Action, Finding, Scope
from unclog.findings.claude_md_context import ClaudeMdContext, ScopedClaudeMd
from unclog.findings.thresholds import Thresholds
from unclog.scan.claude_md import MeasuredSection
from unclog.scan.stats import ActivityIndex
from unclog.state import InstallationState


def detect(
    state: InstallationState,
    activity: ActivityIndex,
    thresholds: Thresholds,
    *,
    now: datetime,
    context: ClaudeMdContext,
) -> list[Finding]:
    # body_hash -> list of (scoped, measured)
    by_hash: dict[str, list[tuple[ScopedClaudeMd, MeasuredSection]]] = defaultdict(list)
    for scoped in context.files:
        for measured in scoped.parsed.sections:
            # Skip preamble and empty-body sections — hashing empty strings
            # conflates unrelated "no body yet" sections.
            if measured.section.heading_level == 0:
                continue
            if not measured.body_hash:
                continue
            if not measured.section.body.strip():
                continue
            by_hash[measured.body_hash].append((scoped, measured))

    findings: list[Finding] = []
    for body_hash, matches in by_hash.items():
        if len(matches) < 2:
            continue
        global_matches = [m for m in matches if m[0].scope_kind == "global"]
        project_matches = [m for m in matches if m[0].scope_kind == "project"]
        if not global_matches or not project_matches:
            # Pure-project duplication is handled by scope_mismatch.
            continue
        # One finding per (global section, aggregated project list).
        for global_scoped, global_measured in global_matches:
            findings.append(
                _build(body_hash, global_scoped, global_measured, project_matches)
            )
    findings.sort(key=lambda f: f.token_savings or 0, reverse=True)
    return findings


def _build(
    body_hash: str,
    global_scoped: ScopedClaudeMd,
    global_measured: MeasuredSection,
    project_matches: list[tuple[ScopedClaudeMd, MeasuredSection]],
) -> Finding:
    section = global_measured.section
    heading = section.heading_text or "<preamble>"
    project_labels = sorted(
        m[0].project_path.name
        for m in project_matches
        if m[0].project_path is not None
    )
    return Finding(
        id=f"claude_md_duplicate:{global_scoped.parsed.path}:{section.start_line}:{heading}",
        type="claude_md_duplicate",
        title=f"Remove duplicated section {heading!r} from global CLAUDE.md",
        reason=(
            f"identical body already in {len(project_matches)} project(s): "
            f"{', '.join(project_labels) or 'unnamed project'}"
        ),
        scope=Scope(kind="global"),
        action=Action(
            primitive="remove_claude_md_section",
            path=global_scoped.parsed.path,
            heading=heading,
        ),
        auto_checked=True,
        token_savings=global_measured.tokens,
        evidence={
            "body_hash": body_hash,
            "heading": heading,
            "heading_path": list(section.heading_path),
            "tokens": global_measured.tokens,
            "global_path": str(global_scoped.parsed.path),
            "project_paths": [
                str(m[0].project_path)
                for m in project_matches
                if m[0].project_path is not None
            ],
        },
    )
