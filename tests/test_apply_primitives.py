from __future__ import annotations

import json
from pathlib import Path

import pytest

from unclog.apply.primitives import ApplyError, apply_action
from unclog.findings.base import Action, Finding, Scope


def _finding(
    *,
    fid: str,
    type_: str = "agent_inventory",
    primitive: str = "delete_file",
    path: Path | None = None,
    server_name: str | None = None,
    scope: Scope | None = None,
) -> Finding:
    return Finding(
        id=fid,
        type=type_,  # type: ignore[arg-type]
        title="t",
        scope=scope if scope is not None else Scope(kind="global"),
        action=Action(
            primitive=primitive,  # type: ignore[arg-type]
            path=path,
            server_name=server_name,
        ),
    )


# -- delete_file ------------------------------------------------------------


def test_delete_file_removes_target_file(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    skill_md = claude_home / "skills" / "ghost" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("body\n", encoding="utf-8")

    finding = _finding(fid="skill:ghost", type_="skill_inventory", path=skill_md)
    apply_action(finding, claude_home=claude_home)

    assert not skill_md.exists()
    # Parent dir is deliberately left behind.
    assert skill_md.parent.is_dir()


def test_delete_file_removes_target_directory(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    skill_dir = claude_home / "skills" / "ghost"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("body\n", encoding="utf-8")

    finding = _finding(fid="skill:ghost", type_="skill_inventory", path=skill_dir)
    apply_action(finding, claude_home=claude_home)

    assert not skill_dir.exists()


def test_delete_file_raises_without_path(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    finding = _finding(fid="x", type_="agent_inventory")
    with pytest.raises(ApplyError, match="missing its target path"):
        apply_action(finding, claude_home=claude_home)


def test_delete_file_raises_when_target_missing(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    finding = _finding(
        fid="x", type_="agent_inventory", path=tmp_path / "ghost" / "missing.md"
    )
    with pytest.raises(ApplyError, match="does not exist"):
        apply_action(finding, claude_home=claude_home)


def test_delete_file_unlinks_symlinked_skill_dir(tmp_path: Path) -> None:
    """Regression: ``shutil.rmtree`` refuses symlinks (GH-46010)."""
    claude_home = tmp_path / ".claude"
    skill_dir = claude_home / "skills" / "gsap"
    skill_dir.parent.mkdir(parents=True)
    backing = tmp_path / ".agents" / "skills" / "gsap"
    backing.mkdir(parents=True)
    (backing / "SKILL.md").write_text("shared body\n", encoding="utf-8")
    skill_dir.symlink_to(backing)

    finding = _finding(fid="skill:gsap", type_="skill_inventory", path=skill_dir)
    apply_action(finding, claude_home=claude_home)

    # Link is gone, backing asset untouched.
    assert not skill_dir.exists() and not skill_dir.is_symlink()
    assert (backing / "SKILL.md").read_text(encoding="utf-8") == "shared body\n"


# -- remove_mcp -------------------------------------------------------------


def test_remove_mcp_deletes_global_server_key(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    config_path = claude_home / ".claude.json"
    config_path.write_text(
        json.dumps({"mcpServers": {"github": {"command": "gh"}, "notion": {"command": "nt"}}}),
        encoding="utf-8",
    )
    finding = _finding(
        fid="mcp:notion",
        type_="mcp_inventory",
        primitive="remove_mcp",
        server_name="notion",
    )
    apply_action(finding, claude_home=claude_home)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["mcpServers"] == {"github": {"command": "gh"}}


def test_remove_mcp_deletes_stale_disabled_prefix_entries(tmp_path: Path) -> None:
    """Leftovers from the old soft-disable scheme remove by their literal key."""
    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    config_path = claude_home / ".claude.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {"command": "gh"},
                    "__unclog_disabled__notion": {"command": "nt"},
                }
            }
        ),
        encoding="utf-8",
    )
    finding = _finding(
        fid="mcp:__unclog_disabled__notion",
        type_="mcp_inventory",
        primitive="remove_mcp",
        server_name="__unclog_disabled__notion",
    )
    apply_action(finding, claude_home=claude_home)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["mcpServers"] == {"github": {"command": "gh"}}


def test_remove_mcp_raises_apply_error_on_malformed_json(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    (claude_home / ".claude.json").write_text("{not valid json,,}", encoding="utf-8")
    finding = _finding(
        fid="mcp:x",
        type_="mcp_inventory",
        primitive="remove_mcp",
        server_name="x",
    )
    with pytest.raises(ApplyError, match="not valid JSON"):
        apply_action(finding, claude_home=claude_home)


def test_remove_mcp_errors_when_server_missing(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    (claude_home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"other": {"command": "o"}}}), encoding="utf-8"
    )
    finding = _finding(
        fid="mcp:ghost",
        type_="mcp_inventory",
        primitive="remove_mcp",
        server_name="ghost",
    )
    with pytest.raises(ApplyError, match="no longer listed"):
        apply_action(finding, claude_home=claude_home)


def test_remove_mcp_deletes_project_scoped_server(tmp_path: Path) -> None:
    """Project-scoped MCP finding edits projects.<abs>.mcpServers, not root."""
    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    project = tmp_path / "myproj"
    project.mkdir()
    config_path = claude_home / ".claude.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {"global_only": {"command": "g"}},
                "projects": {
                    str(project): {
                        "mcpServers": {
                            "proj_mcp": {"command": "p"},
                            "other": {"command": "o"},
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    finding = _finding(
        fid="mcp:proj_mcp",
        type_="mcp_inventory",
        primitive="remove_mcp",
        server_name="proj_mcp",
        scope=Scope(kind="project", project_path=project),
    )
    apply_action(finding, claude_home=claude_home)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["mcpServers"] == {"global_only": {"command": "g"}}
    assert data["projects"][str(project)]["mcpServers"] == {"other": {"command": "o"}}


def test_remove_mcp_errors_when_project_missing_from_config(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    project = tmp_path / "gone"
    project.mkdir()
    (claude_home / ".claude.json").write_text(
        json.dumps({"mcpServers": {}, "projects": {}}), encoding="utf-8"
    )
    finding = _finding(
        fid="mcp:ghost",
        type_="mcp_inventory",
        primitive="remove_mcp",
        server_name="ghost",
        scope=Scope(kind="project", project_path=project),
    )
    with pytest.raises(ApplyError, match="no longer in"):
        apply_action(finding, claude_home=claude_home)
