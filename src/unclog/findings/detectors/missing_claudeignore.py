"""Detect projects that should have a ``.claudeignore`` but don't.

Heuristic: if a project directory still exists on disk and contains
``node_modules`` or ``.venv`` at its root, a missing ``.claudeignore``
means Claude Code may traverse thousands of files it has no business
reading. Flag-only (spec §6, §6.1) — we do not ship a default
``.claudeignore`` in v0.1; the user writes their own.

Iterates ``state.project_scopes`` — the post-narrowed project set —
so ``--project PATH`` correctly scopes the detector to the single
project the user asked about (spec §8.1). Stale entries (paths that
no longer exist on disk) are skipped silently so we don't nag about
removed projects.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from unclog.findings.base import Action, Finding, Scope
from unclog.findings.thresholds import Thresholds
from unclog.scan.stats import ActivityIndex
from unclog.state import InstallationState

_TRIGGERING_SUBDIRS = ("node_modules", ".venv", "venv", "target", "build", "dist")


def detect(
    state: InstallationState,
    activity: ActivityIndex,
    thresholds: Thresholds,
    *,
    now: datetime,
) -> list[Finding]:
    findings: list[Finding] = []
    for project in sorted(state.project_scopes, key=lambda p: p.path):
        if not project.exists:
            continue
        if project.has_claudeignore:
            continue
        trigger = _first_matching_subdir(project.path)
        if trigger is None:
            continue
        findings.append(
            Finding(
                id=f"missing_claudeignore:{project.path}",
                type="missing_claudeignore",
                title=f"Add .claudeignore to {project.name}",
                reason=f"project contains {trigger}/ but no .claudeignore",
                scope=Scope(kind="project", project_path=project.path),
                action=Action(
                    primitive="flag_only",
                    path=project.path / ".claudeignore",
                ),
                auto_checked=False,
                token_savings=None,
                evidence={
                    "project_path": str(project.path),
                    "triggering_subdir": trigger,
                    "note": (
                        "v0.1 does not auto-generate .claudeignore; user writes "
                        "one based on their project layout"
                    ),
                },
            )
        )
    return findings


def _first_matching_subdir(project_path: Path) -> str | None:
    for name in _TRIGGERING_SUBDIRS:
        if (project_path / name).is_dir():
            return name
    return None
