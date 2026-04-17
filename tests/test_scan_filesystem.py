from __future__ import annotations

import json
from pathlib import Path

from unclog.scan.filesystem import (
    enumerate_agents,
    enumerate_commands,
    enumerate_skills,
    load_installed_plugins,
)

SKILL_BODY = "This is the body.\nWith two lines.\n"


def _write_skill(skills_dir: Path, slug: str, frontmatter: str, body: str = SKILL_BODY) -> Path:
    d = skills_dir / slug
    d.mkdir(parents=True)
    skill_md = d / "SKILL.md"
    skill_md.write_text(f"---\n{frontmatter}---\n{body}", encoding="utf-8")
    return skill_md


def test_enumerate_skills_returns_empty_for_missing_dir(tmp_path: Path) -> None:
    assert enumerate_skills(tmp_path / "nope") == ()


def test_enumerate_skills_parses_frontmatter(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(
        skills_dir,
        "code-reviewer",
        frontmatter='name: code-reviewer\ndescription: "Reviews code thoroughly."\nmodel: sonnet\n',
    )
    skills = enumerate_skills(skills_dir)
    assert len(skills) == 1
    s = skills[0]
    assert s.name == "code-reviewer"
    assert s.slug == "code-reviewer"
    assert s.description == "Reviews code thoroughly."
    assert s.model == "sonnet"
    assert s.frontmatter_bytes > 0
    assert s.body_bytes == len(SKILL_BODY.encode("utf-8"))
    assert s.total_dir_bytes >= s.frontmatter_bytes + s.body_bytes


def test_enumerate_skills_falls_back_to_dir_name_without_frontmatter(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    d = skills_dir / "plain-skill"
    d.mkdir()
    (d / "SKILL.md").write_text("No frontmatter here\n", encoding="utf-8")
    skills = enumerate_skills(skills_dir)
    assert len(skills) == 1
    assert skills[0].name == "plain-skill"
    assert skills[0].description is None
    assert skills[0].frontmatter_bytes == 0
    assert skills[0].body_bytes == len(b"No frontmatter here\n")


def test_enumerate_skills_skips_dirs_without_skill_md(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "empty").mkdir()
    (skills_dir / "has-it").mkdir()
    (skills_dir / "has-it" / "SKILL.md").write_text("hi", encoding="utf-8")
    skills = enumerate_skills(skills_dir)
    assert [s.slug for s in skills] == ["has-it"]


def test_enumerate_skills_sums_total_dir_bytes(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "heavy", frontmatter="name: heavy\n", body="body")
    (skills_dir / "heavy" / "reference.md").write_text("x" * 500, encoding="utf-8")
    skills = enumerate_skills(skills_dir)
    assert len(skills) == 1
    assert skills[0].total_dir_bytes >= 500 + skills[0].frontmatter_bytes + skills[0].body_bytes


def test_enumerate_skills_handles_unterminated_frontmatter(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    d = skills_dir / "borked"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: borked\nno closing fence\n", encoding="utf-8")
    skills = enumerate_skills(skills_dir)
    assert len(skills) == 1
    # Unterminated frontmatter → whole file is body, name falls back to dir.
    assert skills[0].name == "borked"
    assert skills[0].frontmatter_bytes == 0


def test_enumerate_agents_reads_frontmatter(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Reviews PRs\n---\nbody\n",
        encoding="utf-8",
    )
    (agents_dir / "not-md.txt").write_text("ignored", encoding="utf-8")
    agents = enumerate_agents(agents_dir)
    assert len(agents) == 1
    assert agents[0].name == "reviewer"
    assert agents[0].description == "Reviews PRs"


def test_enumerate_agents_empty_for_missing_dir(tmp_path: Path) -> None:
    assert enumerate_agents(tmp_path / "nope") == ()


def test_enumerate_agents_recurses_into_category_dirs(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    (agents_dir / "design").mkdir(parents=True)
    (agents_dir / "engineering").mkdir(parents=True)
    (agents_dir / "design" / "ui-designer.md").write_text(
        "---\nname: ui-designer\ndescription: designs UI\n---\nbody\n",
        encoding="utf-8",
    )
    (agents_dir / "engineering" / "backend.md").write_text(
        "---\nname: backend\ndescription: builds APIs\n---\nbody\n",
        encoding="utf-8",
    )
    agents = enumerate_agents(agents_dir)
    assert {a.slug for a in agents} == {"ui-designer", "backend"}


def test_enumerate_agents_skips_files_without_agent_frontmatter(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    # README-style file: no agent frontmatter.
    (agents_dir / "README.md").write_text("# Agents\n\nhow-to guide\n", encoding="utf-8")
    # LICENSE-ish file: frontmatter but missing `description`.
    (agents_dir / "LICENSE.md").write_text(
        "---\nname: license\n---\nMIT\n", encoding="utf-8"
    )
    # Real agent.
    (agents_dir / "real.md").write_text(
        "---\nname: real\ndescription: does work\n---\nbody\n",
        encoding="utf-8",
    )
    agents = enumerate_agents(agents_dir)
    assert [a.slug for a in agents] == ["real"]


def test_enumerate_agents_dedupes_by_slug(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    (agents_dir / "a").mkdir(parents=True)
    (agents_dir / "b").mkdir(parents=True)
    (agents_dir / "a" / "dup.md").write_text(
        "---\nname: dup\ndescription: first\n---\nA\n", encoding="utf-8"
    )
    (agents_dir / "b" / "dup.md").write_text(
        "---\nname: dup\ndescription: second\n---\nB\n", encoding="utf-8"
    )
    agents = enumerate_agents(agents_dir)
    assert len(agents) == 1
    # Lexical path order: a/ sorts before b/.
    assert agents[0].path.parent.name == "a"


def test_enumerate_commands_lists_markdown_files(tmp_path: Path) -> None:
    commands_dir = tmp_path / "commands"
    commands_dir.mkdir()
    (commands_dir / "foo.md").write_text("hello", encoding="utf-8")
    (commands_dir / "bar.md").write_text("hi there", encoding="utf-8")
    (commands_dir / "skip.txt").write_text("ignored", encoding="utf-8")
    commands = enumerate_commands(commands_dir)
    assert sorted(c.slug for c in commands) == ["bar", "foo"]
    total = {c.slug: c.total_bytes for c in commands}
    assert total["foo"] == 5
    assert total["bar"] == 8


def test_load_installed_plugins_parses_plugins_array(tmp_path: Path) -> None:
    path = tmp_path / "installed_plugins.json"
    path.write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": "superpower",
                        "marketplace": "antonin",
                        "version": "1.2.3",
                        "installPath": "/tmp/plugins/superpower",
                        "installedAt": "2026-01-15T10:00:00Z",
                        "gitCommitSha": "abc123",
                    },
                    {"not-a-dict": True},  # skipped
                    {"missing-name": "skipped"},
                ]
            }
        ),
        encoding="utf-8",
    )
    plugins = load_installed_plugins(path)
    assert len(plugins) == 1
    p = plugins[0]
    assert p.name == "superpower"
    assert p.version == "1.2.3"
    assert p.install_path == Path("/tmp/plugins/superpower")
    assert p.installed_at == "2026-01-15T10:00:00Z"


def test_load_installed_plugins_handles_missing_file(tmp_path: Path) -> None:
    assert load_installed_plugins(tmp_path / "nope.json") == ()


def test_load_installed_plugins_handles_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "installed_plugins.json"
    path.write_text("not valid json", encoding="utf-8")
    assert load_installed_plugins(path) == ()


def test_load_installed_plugins_accepts_dict_of_name_to_entry(tmp_path: Path) -> None:
    path = tmp_path / "installed_plugins.json"
    path.write_text(
        json.dumps({"foo": {"version": "0.1"}, "bar": {"version": "2.0"}}),
        encoding="utf-8",
    )
    plugins = load_installed_plugins(path)
    names = sorted(p.name for p in plugins)
    assert names == ["bar", "foo"]
