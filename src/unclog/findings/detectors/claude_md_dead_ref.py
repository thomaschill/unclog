"""Flag CLAUDE.md file references that no longer exist on disk.

Two flavours per file:

- **Line-only** dead refs (whole lines that consist of only the ref,
  optionally with a bullet) roll up into one ``remove_claude_md_lines``
  finding per CLAUDE.md. Auto-checked: stripping these can't damage
  prose, and the apply-layer will capture the removed lines in the
  snapshot for restore.
- **Mixed-prose** dead refs roll up into one ``open_in_editor`` finding
  per CLAUDE.md — never auto-checked. The surrounding sentence might
  need rewriting, not just deletion.

A file with refs of only one kind produces one finding; a file with
both produces two findings so the user can apply the safe cleanup and
still see the manual-review queue.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from unclog.findings.base import Action, Finding, Scope
from unclog.findings.claude_md_context import ClaudeMdContext, ScopedClaudeMd
from unclog.findings.thresholds import Thresholds
from unclog.scan.claude_md import DeadRef
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
    findings: list[Finding] = []
    for scoped in context.files:
        if not scoped.parsed.dead_refs:
            continue
        # Root-file refs (including root-level @-imports at depth=1) flow
        # through the existing line-only / mixed split — the action path
        # is this scope's CLAUDE.md so line-stripping is safe.
        root_refs = tuple(
            r for r in scoped.parsed.dead_refs if r.import_depth <= 1
        )
        # Transitive @-imports (depth ≥ 2) live in intermediate files;
        # they group by ``import_parent`` into manual-review findings so
        # the user sees *which file* to edit.
        transitive = tuple(
            r for r in scoped.parsed.dead_refs if r.import_depth > 1
        )
        line_only = tuple(r for r in root_refs if r.line_only)
        mixed = tuple(r for r in root_refs if not r.line_only)
        if line_only:
            findings.append(_build_line_only(scoped, line_only))
        if mixed:
            findings.append(_build_mixed(scoped, mixed))
        findings.extend(_build_transitive(scoped, transitive))
    return findings


def _scope_for(scoped: ScopedClaudeMd) -> Scope:
    return Scope(
        kind="global" if scoped.scope_kind == "global" else "project",
        project_path=scoped.project_path,
    )


def _label(scoped: ScopedClaudeMd) -> str:
    if scoped.scope_kind == "global":
        return f"global {scoped.variant}"
    project_path: Path | None = scoped.project_path
    name = project_path.name if project_path is not None else "project"
    return f"{name}/{scoped.variant}"


def _build_line_only(scoped: ScopedClaudeMd, refs: tuple[DeadRef, ...]) -> Finding:
    label = _label(scoped)
    lines = tuple(sorted({r.line_number for r in refs}))
    return Finding(
        id=f"claude_md_dead_ref:{scoped.parsed.path}:line-only",
        type="claude_md_dead_ref",
        title=f"Remove {len(refs)} dead file ref(s) in {label}",
        reason="each line references a path that no longer exists",
        scope=_scope_for(scoped),
        action=Action(
            primitive="remove_claude_md_lines",
            path=scoped.parsed.path,
            line_numbers=lines,
        ),
        auto_checked=True,
        token_savings=None,
        evidence={
            "path": str(scoped.parsed.path),
            "variant": scoped.variant,
            "line_count": len(lines),
            "paths": [str(r.resolved) for r in refs],
        },
    )


def _build_transitive(
    scoped: ScopedClaudeMd, refs: tuple[DeadRef, ...]
) -> list[Finding]:
    """One open_in_editor finding per intermediate file with broken @-imports.

    Transitive imports live in files Claude Code inlines via the root
    CLAUDE.md; we can't strip a line from the root to fix them. Grouping
    by parent file points the user at the exact file they need to edit.
    """
    if not refs:
        return []
    by_parent: dict[Path, list[DeadRef]] = {}
    for ref in refs:
        parent = ref.import_parent
        if parent is None:
            continue
        by_parent.setdefault(parent, []).append(ref)
    label = _label(scoped)
    findings: list[Finding] = []
    for parent, parent_refs in sorted(by_parent.items(), key=lambda kv: str(kv[0])):
        lines = tuple(sorted({r.line_number for r in parent_refs}))
        first_line = lines[0] if lines else None
        depths = sorted({r.import_depth for r in parent_refs})
        findings.append(
            Finding(
                id=(
                    f"claude_md_dead_ref:{scoped.parsed.path}:import:{parent}"
                ),
                type="claude_md_dead_ref",
                title=(
                    f"Fix {len(parent_refs)} broken @-import(s) in {parent.name} "
                    f"(reached via {label})"
                ),
                reason=(
                    "transitive @-import targets no longer exist — edit the "
                    "intermediate file to remove or retarget them"
                ),
                scope=_scope_for(scoped),
                action=Action(
                    primitive="open_in_editor",
                    path=parent,
                    line_numbers=(first_line,) if first_line is not None else (),
                ),
                auto_checked=False,
                token_savings=None,
                evidence={
                    "path": str(parent),
                    "root_path": str(scoped.parsed.path),
                    "variant": scoped.variant,
                    "line_numbers": list(lines),
                    "paths": [str(r.resolved) for r in parent_refs],
                    "import_depths": depths,
                },
            )
        )
    return findings


def _build_mixed(scoped: ScopedClaudeMd, refs: tuple[DeadRef, ...]) -> Finding:
    label = _label(scoped)
    lines = tuple(sorted({r.line_number for r in refs}))
    first_line = lines[0] if lines else None
    return Finding(
        id=f"claude_md_dead_ref:{scoped.parsed.path}:mixed",
        type="claude_md_dead_ref",
        title=f"Review {len(refs)} dead ref(s) mixed with prose in {label}",
        reason="file paths no longer exist but surrounding prose may still be useful",
        scope=_scope_for(scoped),
        action=Action(
            primitive="open_in_editor",
            path=scoped.parsed.path,
            line_numbers=(first_line,) if first_line is not None else (),
        ),
        auto_checked=False,
        token_savings=None,
        evidence={
            "path": str(scoped.parsed.path),
            "variant": scoped.variant,
            "line_numbers": list(lines),
            "paths": [str(r.resolved) for r in refs],
        },
    )
