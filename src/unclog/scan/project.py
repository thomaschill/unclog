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

from unclog.scan.config import ConfigParseError, Hook, load_settings


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
    # Auto-memory: ``~/.claude/projects/<encoded>/memory/MEMORY.md``. Claude
    # Code injects this into every session's system prompt (truncated past
    # ~200 lines) so it bloats the baseline the same way CLAUDE.md does.
    # Empty path + blank text when the file doesn't exist on disk.
    memory_md_path: Path = Path()
    memory_md_text: str = ""
    memory_md_bytes: int = 0
    # Project-scoped hooks parsed from ``<project>/.claude/settings.json``
    # and ``<project>/.claude/settings.local.json``. Flattened into a
    # single tuple so consumers don't re-walk the nested event/matcher
    # shape. Empty when no hooks are configured.
    hooks: tuple[Hook, ...] = ()


def _read_text_if_exists(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def scan_project(
    project_path: Path,
    *,
    memory_file: Path | None = None,
) -> ProjectScope:
    """Scan a single project directory into a :class:`ProjectScope`.

    The returned scope is valid even when ``project_path`` doesn't
    exist — callers use ``scope.exists`` to decide whether to skip it
    entirely, surface it as a stale-entry warning, or include it in
    findings.

    ``memory_file`` optionally points to the project's auto-memory
    index (``~/.claude/projects/<encoded>/memory/MEMORY.md``). When
    provided and the file exists, its contents feed the baseline-token
    accounting. When ``None``, memory fields stay empty — tests that
    don't care about memory can omit it.

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

    memory_path = memory_file if memory_file is not None else Path()
    memory_text = _read_text_if_exists(memory_path) if memory_file is not None else ""

    hooks = _load_project_hooks(resolved) if exists else ()

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
        memory_md_path=memory_path,
        memory_md_text=memory_text,
        memory_md_bytes=len(memory_text.encode("utf-8")),
        hooks=hooks,
    )


def _load_project_hooks(project_path: Path) -> tuple[Hook, ...]:
    """Parse hooks from ``<project>/.claude/settings.json`` + ``settings.local.json``.

    Both files are optional. Unparseable JSON is swallowed — a hook
    scanner should not be the thing that crashes a scan. Returns an
    empty tuple when nothing is configured.
    """
    collected: list[Hook] = []
    for filename in ("settings.json", "settings.local.json"):
        settings_path = project_path / ".claude" / filename
        try:
            settings = load_settings(settings_path, source_scope="project")
        except ConfigParseError:
            continue
        if settings is None:
            continue
        collected.extend(settings.hooks)
    return tuple(collected)


def resolve_project_paths(
    *,
    explicit_project: Path | None,
    cwd: Path,
    known_projects: tuple[Path, ...],
) -> tuple[Path, ...]:
    """Return the ordered set of project paths to audit.

    - ``--project PATH`` narrows to exactly that path.
    - Otherwise, audit every known project in ``~/.claude.json`` plus
      ``cwd`` if ``cwd`` looks project-like but isn't yet registered.
      The full view is the only way cross-project CLAUDE.md duplicates
      and scope-mismatch findings actually surface, so we always take it.

    Paths are resolved and de-duplicated while preserving first-seen
    order so output is stable across runs.
    """
    if explicit_project is not None:
        return (explicit_project.expanduser().resolve(strict=False),)

    seen: list[Path] = []
    seen_set: set[Path] = set()
    for raw in known_projects:
        resolved = raw.expanduser().resolve(strict=False)
        if resolved not in seen_set:
            seen.append(resolved)
            seen_set.add(resolved)

    cwd_resolved = cwd.expanduser().resolve(strict=False)
    looks_like_project = (
        (cwd_resolved / ".claude").is_dir() or (cwd_resolved / "CLAUDE.md").is_file()
    )
    if cwd_resolved not in seen_set and looks_like_project:
        seen.append(cwd_resolved)

    return tuple(seen)
