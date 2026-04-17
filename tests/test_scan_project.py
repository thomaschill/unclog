from __future__ import annotations

from pathlib import Path

from unclog.scan.project import resolve_project_paths, scan_project


def test_scan_project_for_missing_path_returns_non_existing_scope(tmp_path: Path) -> None:
    ghost = tmp_path / "ghost"
    scope = scan_project(ghost)
    assert scope.exists is False
    assert scope.claude_md_text == ""
    assert scope.claude_local_md_text == ""
    assert scope.has_claudeignore is False
    assert scope.name == "ghost"


def test_scan_project_reads_claude_md_and_claudeignore(tmp_path: Path) -> None:
    project = tmp_path / "draper"
    project.mkdir()
    (project / "CLAUDE.md").write_text("# Draper\nuse yarn\n", encoding="utf-8")
    (project / ".claudeignore").write_text("node_modules/\n", encoding="utf-8")
    scope = scan_project(project)
    assert scope.exists is True
    assert scope.name == "draper"
    assert "Draper" in scope.claude_md_text
    assert scope.claude_md_bytes > 0
    assert scope.has_claudeignore is True


def test_scan_project_local_claude_md_read_separately(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    (project / "CLAUDE.local.md").write_text("local only\n", encoding="utf-8")
    scope = scan_project(project)
    assert scope.claude_md_text == ""
    assert scope.claude_local_md_text == "local only\n"


def test_resolve_paths_explicit_project_wins(tmp_path: Path) -> None:
    out = resolve_project_paths(
        explicit_project=tmp_path / "x",
        all_projects=False,
        cwd=tmp_path / "cwd",
        known_projects=(tmp_path / "other",),
    )
    assert out == ((tmp_path / "x").resolve(),)


def test_resolve_paths_all_projects_returns_known(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    out = resolve_project_paths(
        explicit_project=None,
        all_projects=True,
        cwd=tmp_path,
        known_projects=(a, b, a),  # duplicate should collapse
    )
    assert out == (a.resolve(), b.resolve())


def test_resolve_paths_cwd_known_project(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    out = resolve_project_paths(
        explicit_project=None,
        all_projects=False,
        cwd=project,
        known_projects=(project,),
    )
    assert out == (project.resolve(),)


def test_resolve_paths_cwd_has_claude_md(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "CLAUDE.md").write_text("hi\n", encoding="utf-8")
    out = resolve_project_paths(
        explicit_project=None,
        all_projects=False,
        cwd=project,
        known_projects=(),
    )
    assert out == (project.resolve(),)


def test_resolve_paths_default_to_empty_when_cwd_not_project(tmp_path: Path) -> None:
    out = resolve_project_paths(
        explicit_project=None,
        all_projects=False,
        cwd=tmp_path,
        known_projects=(),
    )
    assert out == ()
