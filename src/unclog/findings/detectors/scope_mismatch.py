"""Cross-scope CLAUDE.md findings.

Two directions (spec §6, §8.2):

- **Global → project**: a section living in the global CLAUDE.md that
  only names paths resolving inside one known project. The rule is
  project-specific; every other project pays its context cost for
  nothing. Action: ``move_claude_md_section`` from the global file to
  that project's CLAUDE.md. Not auto-checked — the user might have put
  it in global deliberately as an aspiration.
- **Project → global**: the same body hash repeated in
  ``>= thresholds.promote_min_projects`` project CLAUDE.md files, with
  no matching section in global. Action: ``move_claude_md_section``
  consolidating it into global. Not auto-checked.

v0.1 requires at least one resolved path for the global→project
direction so we don't suggest moves based on prose coincidence. Path
extraction uses the same conservative rules as the dead-ref detector —
``/``, ``~/``, ``./``, ``../`` prefixes plus backtick-wrapped paths.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path

from unclog.findings.base import Action, Finding, Scope
from unclog.findings.claude_md_context import ClaudeMdContext, ScopedClaudeMd
from unclog.findings.thresholds import Thresholds
from unclog.scan.claude_md import MeasuredSection, iter_resolved_paths
from unclog.scan.stats import ActivityIndex
from unclog.state import InstallationState
from unclog.util.paths import ClaudePaths


def detect(
    state: InstallationState,
    activity: ActivityIndex,
    thresholds: Thresholds,
    *,
    now: datetime,
    context: ClaudeMdContext,
) -> list[Finding]:
    findings: list[Finding] = []
    known_projects = _known_project_paths(state)
    global_claude_md = ClaudePaths(home=state.claude_home).claude_md
    findings.extend(_detect_global_to_project(context, known_projects))
    findings.extend(
        _detect_project_to_global(
            context,
            min_projects=thresholds.promote_min_projects,
            global_claude_md=global_claude_md,
        )
    )
    return findings


def _known_project_paths(state: InstallationState) -> tuple[Path, ...]:
    paths: list[Path] = []
    for project in state.project_scopes:
        paths.append(project.path)
    # Include stale known projects from ~/.claude.json so references to
    # a removed project still surface a finding (the directory may be
    # on an unmounted disk rather than gone for good).
    if state.global_scope.config is not None:
        for raw in state.global_scope.config.projects:
            resolved = raw.expanduser().resolve(strict=False)
            if resolved not in paths:
                paths.append(resolved)
    return tuple(paths)


def _is_under(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _detect_global_to_project(
    context: ClaudeMdContext, known_projects: tuple[Path, ...]
) -> list[Finding]:
    if not known_projects:
        return []
    findings: list[Finding] = []
    for scoped in context.files:
        if scoped.scope_kind != "global":
            continue
        for measured in scoped.parsed.sections:
            section = measured.section
            if section.heading_level == 0:
                continue
            referenced = iter_resolved_paths(section.body, scoped.parsed.path.parent)
            if not referenced:
                continue
            owning_project = _single_owning_project(referenced, known_projects)
            if owning_project is None:
                continue
            findings.append(_build_global_to_project(scoped, measured, owning_project))
    return findings


def _single_owning_project(
    referenced: list[Path], known_projects: tuple[Path, ...]
) -> Path | None:
    """Return the one project every referenced path lives under, or ``None``."""
    owning: Path | None = None
    for path in referenced:
        matches = [root for root in known_projects if _is_under(path, root)]
        if not matches:
            return None
        # Prefer the longest match so nested projects don't collide.
        match = max(matches, key=lambda p: len(str(p)))
        if owning is None:
            owning = match
        elif owning != match:
            return None
    return owning


def _build_global_to_project(
    scoped: ScopedClaudeMd,
    measured: MeasuredSection,
    owning_project: Path,
) -> Finding:
    section = measured.section
    heading = section.heading_text or "<preamble>"
    target = owning_project / "CLAUDE.md"
    return Finding(
        id=f"scope_mismatch_global_to_project:{scoped.parsed.path}:{section.start_line}:{heading}",
        type="scope_mismatch_global_to_project",
        title=f"Move {heading!r} into {owning_project.name}/CLAUDE.md",
        reason=f"section only references paths under {owning_project}",
        scope=Scope(kind="global_to_project", project_path=owning_project),
        action=Action(
            primitive="move_claude_md_section",
            path=scoped.parsed.path,
            heading=heading,
        ),
        auto_checked=False,
        token_savings=measured.tokens,
        evidence={
            "source_path": str(scoped.parsed.path),
            "destination_path": str(target),
            "heading": heading,
            "heading_path": list(section.heading_path),
            "tokens": measured.tokens,
        },
    )


def _detect_project_to_global(
    context: ClaudeMdContext,
    *,
    min_projects: int,
    global_claude_md: Path,
) -> list[Finding]:
    by_hash: dict[str, list[tuple[ScopedClaudeMd, MeasuredSection]]] = defaultdict(list)
    global_hashes: set[str] = set()
    for scoped in context.files:
        for measured in scoped.parsed.sections:
            if measured.section.heading_level == 0:
                continue
            if not measured.section.body.strip():
                continue
            if scoped.scope_kind == "global":
                global_hashes.add(measured.body_hash)
                continue
            by_hash[measured.body_hash].append((scoped, measured))

    findings: list[Finding] = []
    for body_hash, matches in by_hash.items():
        if body_hash in global_hashes:
            continue
        distinct_projects = {
            m[0].project_path for m in matches if m[0].project_path is not None
        }
        if len(distinct_projects) < min_projects:
            continue
        findings.append(
            _build_project_to_global(
                body_hash, matches, distinct_projects, global_claude_md
            )
        )
    return findings


def _build_project_to_global(
    body_hash: str,
    matches: list[tuple[ScopedClaudeMd, MeasuredSection]],
    distinct_projects: set[Path],
    global_claude_md: Path,
) -> Finding:
    # Choose the first match as the canonical source; its heading drives the id.
    canonical_scoped, canonical_measured = matches[0]
    section = canonical_measured.section
    heading = section.heading_text or "<preamble>"
    project_names = sorted(p.name for p in distinct_projects)
    return Finding(
        id=f"scope_mismatch_project_to_global:{body_hash}:{heading}",
        type="scope_mismatch_project_to_global",
        title=f"Promote {heading!r} to global CLAUDE.md",
        reason=(
            f"identical section in {len(distinct_projects)} project CLAUDE.md files: "
            f"{', '.join(project_names)}"
        ),
        scope=Scope(kind="project_to_global"),
        action=Action(
            primitive="move_claude_md_section",
            path=canonical_scoped.parsed.path,
            heading=heading,
        ),
        auto_checked=False,
        token_savings=canonical_measured.tokens,
        evidence={
            "body_hash": body_hash,
            "heading": heading,
            "heading_path": list(section.heading_path),
            "tokens_per_project": canonical_measured.tokens,
            "project_paths": [str(p) for p in sorted(distinct_projects)],
            "source_path": str(canonical_scoped.parsed.path),
            "destination_path": str(global_claude_md),
        },
    )
