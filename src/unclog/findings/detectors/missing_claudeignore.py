"""Detect projects that should have a ``.claudeignore`` but don't.

Heuristic: if a project directory still exists on disk and contains
``node_modules`` or ``.venv`` at its root, a missing ``.claudeignore``
means Claude Code may traverse thousands of files it has no business
reading. Flag-only (spec §6, §6.1) — we do not ship a default
``.claudeignore`` in v0.1; the user writes their own.

Project list comes from ``~/.claude.json``'s ``projects`` map. Stale
entries (paths that no longer exist on disk) are skipped silently so
we don't nag about removed projects.
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
    config = state.global_scope.config
    if config is None or not config.projects:
        return []

    findings: list[Finding] = []
    for project_path in sorted(config.projects):
        if not project_path.is_dir():
            continue
        if (project_path / ".claudeignore").exists():
            continue
        trigger = _first_matching_subdir(project_path)
        if trigger is None:
            continue
        findings.append(
            Finding(
                id=f"missing_claudeignore:{project_path}",
                type="missing_claudeignore",
                title=f"Add .claudeignore to {project_path.name}",
                reason=f"project contains {trigger}/ but no .claudeignore",
                scope=Scope(kind="project", project_path=project_path),
                action=Action(
                    primitive="flag_only",
                    path=project_path / ".claudeignore",
                ),
                auto_checked=False,
                token_savings=None,
                evidence={
                    "project_path": str(project_path),
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
