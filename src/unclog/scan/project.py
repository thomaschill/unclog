"""Per-project scope enumeration.

A :class:`ProjectScope` is the project-side counterpart of the
:class:`~unclog.state.GlobalScope` already scanned under
``~/.claude/``. For M4 it carries just enough information to drive
the CLAUDE.md lint passes and ``missing_claudeignore`` improvements:

- the project's absolute path and human-readable name,
- both CLAUDE.md variants (``CLAUDE.md`` and ``CLAUDE.local.md``),
- whether a ``.claudeignore`` is present,
- whether the path still exists on disk (stale ``.claude.json`` entries
  should not break the scan).

Per-project skills / agents / commands and per-project session JSONL
are deferred to later milestones — M4 only needs CLAUDE.md plumbing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectScope:
    """Everything scanned under a single project directory."""

    path: Path
    name: str
    exists: bool
    claude_md_path: Path
    claude_md_text: str
    claude_md_bytes: int
    claude_local_md_path: Path
    claude_local_md_text: str
    claude_local_md_bytes: int
    has_claudeignore: bool


def _read_text_if_exists(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def scan_project(project_path: Path) -> ProjectScope:
    """Scan a single project directory into a :class:`ProjectScope`.

    The returned scope is valid even when ``project_path`` doesn't
    exist — callers (``--all-projects``) use ``scope.exists`` to decide
    whether to skip it entirely, surface it as a stale-entry warning,
    or include it in findings.

    Name resolution is last-segment-of-path in v0.1. Project-config
    name overrides are listed in spec §8 as future work (we don't read
    ``.claude/settings.json``'s name-related fields yet).
    """
    path = project_path.expanduser()
    # Best-effort resolve; non-existent paths keep their literal form.
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path

    claude_md_path = resolved / "CLAUDE.md"
    claude_local_md_path = resolved / "CLAUDE.local.md"
    claudeignore_path = resolved / ".claudeignore"

    exists = resolved.is_dir()
    claude_md_text = _read_text_if_exists(claude_md_path) if exists else ""
    claude_local_md_text = _read_text_if_exists(claude_local_md_path) if exists else ""

    return ProjectScope(
        path=resolved,
        name=resolved.name or str(resolved),
        exists=exists,
        claude_md_path=claude_md_path,
        claude_md_text=claude_md_text,
        claude_md_bytes=len(claude_md_text.encode("utf-8")),
        claude_local_md_path=claude_local_md_path,
        claude_local_md_text=claude_local_md_text,
        claude_local_md_bytes=len(claude_local_md_text.encode("utf-8")),
        has_claudeignore=exists and claudeignore_path.exists(),
    )


def resolve_project_paths(
    *,
    explicit_project: Path | None,
    all_projects: bool,
    cwd: Path,
    known_projects: tuple[Path, ...],
) -> tuple[Path, ...]:
    """Return the ordered set of project paths to audit.

    Priority rules from spec §8.1:

    - ``--project PATH`` wins and returns exactly that path.
    - ``--all-projects`` returns every entry from ``~/.claude.json``.
    - Otherwise, if ``cwd`` itself looks project-like (either present
      in ``known_projects`` or carrying ``.claude/`` / ``CLAUDE.md``),
      return just ``cwd``.
    - Otherwise, return no projects: global-only scan.

    Paths are resolved and de-duplicated while preserving first-seen
    order so output is stable across runs.
    """
    if explicit_project is not None:
        return (explicit_project.expanduser().resolve(strict=False),)

    if all_projects:
        seen: list[Path] = []
        for raw in known_projects:
            resolved = raw.expanduser().resolve(strict=False)
            if resolved not in seen:
                seen.append(resolved)
        return tuple(seen)

    cwd_resolved = cwd.expanduser().resolve(strict=False)
    if cwd_resolved in {p.expanduser().resolve(strict=False) for p in known_projects}:
        return (cwd_resolved,)
    if (cwd_resolved / ".claude").is_dir() or (cwd_resolved / "CLAUDE.md").is_file():
        return (cwd_resolved,)
    return ()
