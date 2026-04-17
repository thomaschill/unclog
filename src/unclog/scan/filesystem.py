"""Enumerate skills, agents, commands, and installed plugins.

Walks the conventional directory layouts Claude Code writes and returns
immutable records with size + basic metadata. Intentionally minimal for
M1: frontmatter parsing is line-oriented and recognises string-scalar
keys (``name``, ``description``, ``model``) only. Richer YAML support
lands when we actually need it.
"""

from __future__ import annotations

import json
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
    """A slash command discovered at ``<scope>/commands/<slug>.md``."""

    name: str
    slug: str
    path: Path
    total_bytes: int


@dataclass(frozen=True)
class InstalledPlugin:
    """A record from ``~/.claude/plugins/installed_plugins.json``."""

    name: str
    marketplace: str | None
    version: str | None
    install_path: Path | None
    installed_at: str | None
    git_commit_sha: str | None


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
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                continue
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
    for entry in sorted(agents_dir.rglob("*.md")):
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
    """Return every slash command found under ``commands_dir``."""
    if not commands_dir.is_dir():
        return ()
    commands: list[Command] = []
    for entry in sorted(commands_dir.iterdir()):
        if not entry.is_file() or entry.suffix != ".md":
            continue
        try:
            size = entry.stat().st_size
        except OSError:
            continue
        slug = entry.stem
        commands.append(Command(name=slug, slug=slug, path=entry, total_bytes=size))
    return tuple(commands)


def load_installed_plugins(path: Path) -> tuple[InstalledPlugin, ...]:
    """Read ``installed_plugins.json`` into typed records.

    Returns an empty tuple if the file is missing or malformed. Unknown
    fields are ignored; missing scalar fields become ``None``.
    """
    if not path.is_file():
        return ()
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()

    entries: list[object] = []
    if isinstance(raw, dict):
        plugins_field = raw.get("plugins")
        if isinstance(plugins_field, list):
            entries = list(plugins_field)
        else:
            # Some historical layouts key plugins by name at the top level.
            for name, value in raw.items():
                if isinstance(name, str) and isinstance(value, dict):
                    entries.append({**value, "name": value.get("name", name)})
    elif isinstance(raw, list):
        entries = list(raw)

    parsed: list[InstalledPlugin] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        install_path_raw = entry.get("installPath")
        parsed.append(
            InstalledPlugin(
                name=name,
                marketplace=entry.get("marketplace")
                if isinstance(entry.get("marketplace"), str)
                else None,
                version=entry.get("version") if isinstance(entry.get("version"), str) else None,
                install_path=Path(install_path_raw) if isinstance(install_path_raw, str) else None,
                installed_at=entry.get("installedAt")
                if isinstance(entry.get("installedAt"), str)
                else None,
                git_commit_sha=entry.get("gitCommitSha")
                if isinstance(entry.get("gitCommitSha"), str)
                else None,
            )
        )
    return tuple(parsed)
