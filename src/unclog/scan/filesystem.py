"""Enumerate skills, agents, and slash commands from a Claude Code install.

Frontmatter parsing is intentionally line-oriented: recognises string-scalar
keys only (``name``, ``description``). Good enough for the picker's token
estimate; richer YAML lands when we actually need it.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

_FRONTMATTER_DELIM = "---"


@dataclass(frozen=True)
class Skill:
    """A skill discovered at ``<scope>/skills/<slug>/SKILL.md``."""

    name: str
    slug: str
    directory: Path
    description: str | None


@dataclass(frozen=True)
class Agent:
    """An agent discovered at ``<scope>/agents/<slug>.md``."""

    name: str
    slug: str
    path: Path
    description: str | None


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


def _parse_frontmatter(text: str) -> Mapping[str, str]:
    """Return the key/value pairs from a ``---``-fenced YAML header.

    Empty mapping if the text doesn't open with a fence or the fence is
    unterminated. Unknown shapes are skipped silently — the picker just
    falls back to the filename stem when ``name`` is missing.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != _FRONTMATTER_DELIM:
        return {}

    end_index: int | None = None
    for i, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n") == _FRONTMATTER_DELIM:
            end_index = i
            break
    if end_index is None:
        return {}

    parsed: dict[str, str] = {}
    for line in lines[1:end_index]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith(("'", '"')) and value.endswith(value[0]) and len(value) >= 2:
            value = value[1:-1]
        if key:
            parsed[key] = value
    return parsed


def _iter_md_files(root: Path) -> Iterator[Path]:
    """Yield every ``*.md`` under ``root`` in lexical order.

    ``os.walk`` with ``followlinks=False`` prevents a circular directory
    symlink from spinning forever; ``Path.rglob`` follows dir symlinks
    on Python < 3.13, so we don't rely on it.
    """
    md_files: list[Path] = []
    for dirpath, _dirs, files in os.walk(root, followlinks=False):
        for name in files:
            if name.endswith(".md"):
                md_files.append(Path(dirpath) / name)
    return iter(sorted(md_files))


def _read_md_frontmatter(path: Path) -> Mapping[str, str] | None:
    """Read ``path`` and return its frontmatter, or ``None`` on OS error."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return _parse_frontmatter(text)


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
        frontmatter = _read_md_frontmatter(skill_md)
        if frontmatter is None:
            continue
        skills.append(
            Skill(
                name=frontmatter.get("name") or entry.name,
                slug=entry.name,
                directory=entry,
                description=frontmatter.get("description"),
            )
        )
    return tuple(skills)


def enumerate_agents(agents_dir: Path) -> tuple[Agent, ...]:
    """Return every agent found under ``agents_dir`` (recursive).

    Non-agent files like ``README.md`` or ``LICENSE.md`` share the same
    extension but lack agent frontmatter — we filter to files whose YAML
    front-matter declares both ``name`` and ``description``, which is the
    shape Claude's agent loader requires. Duplicate slugs are deduped
    (first wins, lexical-path order) to match the loader's
    last-registration-loses behavior.
    """
    if not agents_dir.is_dir():
        return ()
    agents: list[Agent] = []
    seen_slugs: set[str] = set()
    for entry in _iter_md_files(agents_dir):
        frontmatter = _read_md_frontmatter(entry)
        if frontmatter is None:
            continue
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
            )
        )
    return tuple(agents)


def enumerate_commands(commands_dir: Path) -> tuple[Command, ...]:
    """Return every slash command found under ``commands_dir`` (recursive).

    Any ``.md`` file is a command — the filename stem is the invocation
    name. Frontmatter is optional; when present we surface the
    description for the picker's token estimate.
    """
    if not commands_dir.is_dir():
        return ()
    commands: list[Command] = []
    seen_slugs: set[str] = set()
    for entry in _iter_md_files(commands_dir):
        frontmatter = _read_md_frontmatter(entry)
        if frontmatter is None:
            continue
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
            )
        )
    return tuple(commands)
