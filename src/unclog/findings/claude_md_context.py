"""Shared parse of every CLAUDE.md across scopes.

The ``claude_md_duplicate`` and ``scope_mismatch_*`` detectors both
need to look at the same four-way product: ``CLAUDE.md`` /
``CLAUDE.local.md`` times global / per-project scopes. Building this
once and passing a :class:`ClaudeMdContext` into each detector avoids
re-parsing the same file twice in a row.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from unclog.findings.base import ScopeKind
from unclog.scan.claude_md import ParsedClaudeMd, parse_claude_md
from unclog.scan.tokens import TiktokenCounter, TokenCounter
from unclog.state import InstallationState
from unclog.util.paths import ClaudePaths


@dataclass(frozen=True)
class ScopedClaudeMd:
    """A parsed CLAUDE.md tagged with the scope it came from."""

    parsed: ParsedClaudeMd
    scope_kind: ScopeKind
    project_path: Path | None = None
    variant: str = "CLAUDE.md"  # or "CLAUDE.local.md"


@dataclass(frozen=True)
class ClaudeMdContext:
    """Aggregate of every CLAUDE.md file surfaced by the current scan."""

    files: tuple[ScopedClaudeMd, ...]


def build_context(
    state: InstallationState,
    counter: TokenCounter | None = None,
) -> ClaudeMdContext:
    """Parse every CLAUDE.md + CLAUDE.local.md in ``state`` once.

    Empty files are silently skipped — there is nothing for the
    detectors to say about a file that was never created.
    """
    count_with = counter if counter is not None else TiktokenCounter()
    files: list[ScopedClaudeMd] = []

    paths = ClaudePaths(home=state.claude_home)
    gs = state.global_scope
    if gs.claude_md_text:
        files.append(
            ScopedClaudeMd(
                parsed=parse_claude_md(paths.claude_md, gs.claude_md_text, count_with),
                scope_kind="global",
                project_path=None,
                variant="CLAUDE.md",
            )
        )
    if gs.claude_local_md_text:
        files.append(
            ScopedClaudeMd(
                parsed=parse_claude_md(paths.claude_local_md, gs.claude_local_md_text, count_with),
                scope_kind="global",
                project_path=None,
                variant="CLAUDE.local.md",
            )
        )

    for project in state.project_scopes:
        if not project.exists:
            continue
        if project.claude_md_text:
            files.append(
                ScopedClaudeMd(
                    parsed=parse_claude_md(
                        project.claude_md_path, project.claude_md_text, count_with
                    ),
                    scope_kind="project",
                    project_path=project.path,
                    variant="CLAUDE.md",
                )
            )
        if project.claude_local_md_text:
            files.append(
                ScopedClaudeMd(
                    parsed=parse_claude_md(
                        project.claude_local_md_path,
                        project.claude_local_md_text,
                        count_with,
                    ),
                    scope_kind="project",
                    project_path=project.path,
                    variant="CLAUDE.local.md",
                )
            )

    return ClaudeMdContext(files=tuple(files))
