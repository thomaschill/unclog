"""Enumerate skills, agents, commands, and installed plugins.

Walks the conventional directory layouts Claude Code writes and returns
immutable records with size + basic metadata. Intentionally minimal for
M1: frontmatter parsing is line-oriented and recognises string-scalar
keys (``name``, ``description``, ``model``) only. Richer YAML support
lands when we actually need it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_FRONTMATTER_DELIM = "---"


@dataclass(frozen=True)
class Skill:
    """A skill discovered at ``<scope>/skills/<slug>/SKILL.md``."""

    name: str
    slug: str
    directory: Path
    skill_md_path: Path
    description: str | None
    model: str | None
    frontmatter_bytes: int
    body_bytes: int
    total_dir_bytes: int


@dataclass(frozen=True)
class Agent:
    """An agent discovered at ``<scope>/agents/<slug>.md``."""

    name: str
    slug: str
    path: Path
    description: str | None
    frontmatter_bytes: int
    body_bytes: int


@dataclass(frozen=True)
class Command:
    """A slash command discovered at ``<scope>/commands/<slug>.md``.

    Unlike agents, commands don't require frontmatter — the filename stem
    is the invocation name. A description, if present, comes from optional
    frontmatter and is used for the picker's token estimate.
    """

    name: str
    slug: str
    path: Path
    description: str | None
    frontmatter_bytes: int
    body_bytes: int


def _split_frontmatter(text: str) -> tuple[Mapping[str, str], int, int]:
    """Return (parsed, frontmatter_byte_length, body_byte_length).

    If the text does not open with a ``---`` fence line the entire text
    is treated as body with an empty frontmatter mapping.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != _FRONTMATTER_DELIM:
        return {}, 0, len(text.encode("utf-8"))

    end_index: int | None = None
    for i, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n") == _FRONTMATTER_DELIM:
            end_index = i
            break
    if end_index is None:
        # Unterminated frontmatter — treat the whole file as body.
        return {}, 0, len(text.encode("utf-8"))

    frontmatter_src = "".join(lines[: end_index + 1])
    body_src = "".join(lines[end_index + 1 :])

    parsed: dict[str, str] = {}
    for line in lines[1:end_index]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith(("'", '"')) and value.endswith(value[0]) and len(value) >= 2:
            value = value[1:-1]
        if key:
            parsed[key] = value

    return parsed, len(frontmatter_src.encode("utf-8")), len(body_src.encode("utf-8"))


def _dir_total_bytes(path: Path) -> int:
    """Sum the size of every file under ``path`` without following symlinks.

    ``rglob`` follows directory symlinks by default on Python < 3.13,
    so a skill with a circular symlink (``skills/foo/loop -> ../..``)
    would walk the tree forever. We walk by hand with
    :func:`os.walk` and ``followlinks=False`` to stay safe across
    Python versions.
    """
    total = 0
    try:
        for root, _dirs, files in os.walk(path, followlinks=False):
            for name in files:
                entry = Path(root) / name
                try:
                    if entry.is_symlink():
                        # Don't stat across symlinks — the target might
                        # be huge, shared, or missing; reporting the
                        # link's own size is the honest thing to do.
                        total += entry.lstat().st_size
                    elif entry.is_file():
                        total += entry.stat().st_size
                except OSError:
                    continue
    except OSError:
        return total
    return total


def enumerate_skills(skills_dir: Path) -> tuple[Skill, ...]:
    """Return every skill found under ``skills_dir``.

    A skill is any immediate subdirectory containing a ``SKILL.md`` file.
    Returns an empty tuple if the directory is missing.
    """
    if not skills_dir.is_dir():
        return ()
    skills: list[Skill] = []
    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_dir():
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.is_file():
            continue
        try:
            text = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue
        frontmatter, fm_bytes, body_bytes = _split_frontmatter(text)
        name = frontmatter.get("name") or entry.name
        skills.append(
            Skill(
                name=name,
                slug=entry.name,
                directory=entry,
                skill_md_path=skill_md,
                description=frontmatter.get("description"),
                model=frontmatter.get("model"),
                frontmatter_bytes=fm_bytes,
                body_bytes=body_bytes,
                total_dir_bytes=_dir_total_bytes(entry),
            )
        )
    return tuple(skills)


def enumerate_agents(agents_dir: Path) -> tuple[Agent, ...]:
    """Return every agent found under ``agents_dir`` (recursive).

    Claude Code loads agents from nested category dirs (``agents/design/*.md``,
    ``agents/engineering/*.md``, ...), so we ``rglob``. Non-agent files like
    ``README.md`` or ``LICENSE.md`` share the same extension but lack agent
    frontmatter — we filter to files whose YAML front-matter declares both
    ``name`` and ``description``, which is the shape Claude's agent loader
    requires. Entries with duplicate slugs are deduped (first wins,
    lexical-path order) to match the loader's last-registration-loses behavior.
    """
    if not agents_dir.is_dir():
        return ()
    agents: list[Agent] = []
    seen_slugs: set[str] = set()
    md_files: list[Path] = []
    # ``os.walk`` with ``followlinks=False`` prevents a circular
    # directory symlink from spinning forever; ``Path.rglob`` follows
    # dir symlinks on Python < 3.13, so we don't rely on it.
    for root, _dirs, files in os.walk(agents_dir, followlinks=False):
        for name in files:
            if name.endswith(".md"):
                md_files.append(Path(root) / name)
    for entry in sorted(md_files):
        if not entry.is_file():
            continue
        try:
            text = entry.read_text(encoding="utf-8")
        except OSError:
            continue
        frontmatter, fm_bytes, body_bytes = _split_frontmatter(text)
        if "name" not in frontmatter or "description" not in frontmatter:
            continue
        slug = entry.stem
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        agents.append(
            Agent(
                name=frontmatter.get("name") or slug,
                slug=slug,
                path=entry,
                description=frontmatter.get("description"),
                frontmatter_bytes=fm_bytes,
                body_bytes=body_bytes,
            )
        )
    return tuple(agents)


def enumerate_commands(commands_dir: Path) -> tuple[Command, ...]:
    """Return every slash command found under ``commands_dir`` (recursive).

    Claude Code loads slash commands from nested category dirs the same
    way it does agents (``commands/git/commit.md`` becomes ``/git:commit``
    in some layouts), so we ``rglob``. Any ``.md`` file is treated as a
    command — the filename stem is the invocation name. Frontmatter is
    parsed when present so we can surface the description in the picker,
    but commands without frontmatter are still enumerated.
    """
    if not commands_dir.is_dir():
        return ()
    commands: list[Command] = []
    seen_slugs: set[str] = set()
    md_files: list[Path] = []
    for root, _dirs, files in os.walk(commands_dir, followlinks=False):
        for name in files:
            if name.endswith(".md"):
                md_files.append(Path(root) / name)
    for entry in sorted(md_files):
        if not entry.is_file():
            continue
        try:
            text = entry.read_text(encoding="utf-8")
        except OSError:
            continue
        frontmatter, fm_bytes, body_bytes = _split_frontmatter(text)
        slug = entry.stem
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        commands.append(
            Command(
                name=frontmatter.get("name") or slug,
                slug=slug,
                path=entry,
                description=frontmatter.get("description"),
                frontmatter_bytes=fm_bytes,
                body_bytes=body_bytes,
            )
        )
    return tuple(commands)


