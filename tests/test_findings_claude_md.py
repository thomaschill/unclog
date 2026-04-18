from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

import pytest

from unclog.findings import detect
from unclog.findings.thresholds import Thresholds
from unclog.scan.config import ClaudeConfig, Settings
from unclog.scan.project import scan_project
from unclog.scan.stats import ActivityIndex
from unclog.state import GlobalScope, InstallationState

NOW = datetime(2026, 4, 17, tzinfo=UTC)


def _state(
    *,
    claude_home: Path,
    global_md: str = "",
    projects: tuple[Path, ...] = (),
    config_projects: tuple[Path, ...] = (),
) -> InstallationState:
    project_scopes = tuple(scan_project(p) for p in projects)
    claude_home.mkdir(parents=True, exist_ok=True)
    if global_md:
        (claude_home / "CLAUDE.md").write_text(global_md, encoding="utf-8")
    config: ClaudeConfig | None = None
    if config_projects:
        from unclog.scan.config import ProjectRecord

        config = ClaudeConfig(
            projects=MappingProxyType(
                {p: ProjectRecord(path=p) for p in config_projects}
            ),
        )
    return InstallationState(
        generated_at=NOW,
        claude_home=claude_home,
        global_scope=GlobalScope(
            claude_home=claude_home,
            config=config,
            settings=Settings(),
            claude_md_bytes=len(global_md.encode("utf-8")),
            claude_md_text=global_md,
            claude_local_md_bytes=0,
            claude_local_md_text="",
            activity=ActivityIndex(),
        ),
        project_scopes=project_scopes,
    )


def _defaults() -> Thresholds:
    return Thresholds(unused_days=90, stale_plugin_days=90, promote_min_projects=3)


# -- claude_md_oversized ----------------------------------------------------


def test_oversized_fires_for_sections_over_threshold(tmp_path: Path) -> None:
    # 8000 chars -> tiktoken counts comfortably over 1,000 for English text.
    big = "# Big section\n" + ("use yarn always. " * 600) + "\n"
    state = _state(claude_home=tmp_path / ".claude", global_md=big)
    findings = detect(state, ActivityIndex(), _defaults(), now=NOW)
    oversized = [f for f in findings if f.type == "claude_md_oversized"]
    assert len(oversized) == 1
    f = oversized[0]
    assert f.auto_checked is False
    assert f.action.primitive == "open_in_editor"
    assert f.action.heading == "Big section"


def test_oversized_ignores_small_sections(tmp_path: Path) -> None:
    state = _state(claude_home=tmp_path / ".claude", global_md="# Small\nshort.\n")
    findings = detect(state, ActivityIndex(), _defaults(), now=NOW)
    assert all(f.type != "claude_md_oversized" for f in findings)


# -- claude_md_dead_ref -----------------------------------------------------


def test_dead_ref_splits_line_only_from_mixed_prose(tmp_path: Path) -> None:
    md = "# Refs\n- /nope/only.py\nsee /nope/mixed.py for details\n"
    state = _state(claude_home=tmp_path / ".claude", global_md=md)
    findings = detect(state, ActivityIndex(), _defaults(), now=NOW)
    dead = [f for f in findings if f.type == "claude_md_dead_ref"]
    by_primitive = {f.action.primitive: f for f in dead}
    assert "remove_claude_md_lines" in by_primitive
    assert "open_in_editor" in by_primitive
    assert by_primitive["remove_claude_md_lines"].auto_checked is True
    assert by_primitive["open_in_editor"].auto_checked is False


def test_dead_ref_none_when_all_paths_exist(tmp_path: Path) -> None:
    real_file = tmp_path / "real.txt"
    real_file.write_text("hi", encoding="utf-8")
    md = f"- `{real_file}`\n"
    state = _state(claude_home=tmp_path / ".claude", global_md=md)
    findings = detect(state, ActivityIndex(), _defaults(), now=NOW)
    assert not any(f.type == "claude_md_dead_ref" for f in findings)


def test_transitive_at_import_points_at_intermediate_file(tmp_path: Path) -> None:
    """Deep @-import chain with a broken leaf → open_in_editor on the parent."""
    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    # Root -> mid.md -> nope.md (missing)
    mid = claude_home / "mid.md"
    mid.write_text("@./nope.md\n", encoding="utf-8")
    md = "@./mid.md\n"
    state = _state(claude_home=claude_home, global_md=md)
    findings = detect(state, ActivityIndex(), _defaults(), now=NOW)
    dead = [f for f in findings if f.type == "claude_md_dead_ref"]
    # One transitive finding with action pointing at mid.md, not root.
    transitive = [f for f in dead if f.action.path == mid]
    assert len(transitive) == 1
    assert transitive[0].action.primitive == "open_in_editor"
    assert transitive[0].auto_checked is False
    assert transitive[0].evidence["root_path"].endswith("CLAUDE.md")


# -- claude_md_duplicate ----------------------------------------------------


def test_duplicate_fires_when_global_section_matches_project(tmp_path: Path) -> None:
    shared = "# Yarn rule\nalways use yarn, never npm\n"
    project = tmp_path / "draper"
    project.mkdir()
    (project / "CLAUDE.md").write_text(shared, encoding="utf-8")
    state = _state(
        claude_home=tmp_path / ".claude",
        global_md=shared,
        projects=(project,),
    )
    findings = detect(state, ActivityIndex(), _defaults(), now=NOW)
    dup = [f for f in findings if f.type == "claude_md_duplicate"]
    assert len(dup) == 1
    f = dup[0]
    assert f.scope.kind == "global"
    assert f.action.primitive == "remove_claude_md_section"
    assert f.auto_checked is True
    assert f.token_savings is not None and f.token_savings > 0


def test_duplicate_does_not_fire_across_two_projects(tmp_path: Path) -> None:
    shared = "# Rule\nuse pnpm\n"
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "CLAUDE.md").write_text(shared, encoding="utf-8")
    (b / "CLAUDE.md").write_text(shared, encoding="utf-8")
    state = _state(claude_home=tmp_path / ".claude", projects=(a, b))
    findings = detect(state, ActivityIndex(), _defaults(), now=NOW)
    assert all(f.type != "claude_md_duplicate" for f in findings)


# -- scope_mismatch_global_to_project ---------------------------------------


def test_global_to_project_when_all_paths_live_under_one_project(
    tmp_path: Path,
) -> None:
    project = tmp_path / "draper"
    (project / "src").mkdir(parents=True)
    (project / "src" / "foo.py").write_text("", encoding="utf-8")
    (project / "src" / "bar.py").write_text("", encoding="utf-8")
    md = (
        "# Draper-only rules\n"
        f"- look at `{project}/src/foo.py`\n"
        f"- and `{project}/src/bar.py`\n"
    )
    state = _state(
        claude_home=tmp_path / ".claude",
        global_md=md,
        projects=(project,),
        config_projects=(project,),
    )
    findings = detect(state, ActivityIndex(), _defaults(), now=NOW)
    gtp = [f for f in findings if f.type == "scope_mismatch_global_to_project"]
    assert len(gtp) == 1
    f = gtp[0]
    assert f.scope.kind == "global_to_project"
    assert f.scope.project_path == project.resolve()
    assert f.action.primitive == "move_claude_md_section"
    assert f.auto_checked is False


def test_global_to_project_skipped_when_paths_span_multiple_projects(
    tmp_path: Path,
) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    (a / "src").mkdir(parents=True)
    (b / "src").mkdir(parents=True)
    (a / "src" / "x.py").write_text("", encoding="utf-8")
    (b / "src" / "y.py").write_text("", encoding="utf-8")
    md = f"# Mixed\n- `{a}/src/x.py`\n- `{b}/src/y.py`\n"
    state = _state(
        claude_home=tmp_path / ".claude",
        global_md=md,
        projects=(a, b),
        config_projects=(a, b),
    )
    findings = detect(state, ActivityIndex(), _defaults(), now=NOW)
    assert all(f.type != "scope_mismatch_global_to_project" for f in findings)


# -- scope_mismatch_project_to_global ---------------------------------------


def test_project_to_global_fires_at_threshold(tmp_path: Path) -> None:
    shared = "# Shared rule\nnever commit to main\n"
    projects: list[Path] = []
    for name in ("alpha", "bravo", "charlie"):
        project = tmp_path / name
        project.mkdir()
        (project / "CLAUDE.md").write_text(shared, encoding="utf-8")
        projects.append(project)
    state = _state(claude_home=tmp_path / ".claude", projects=tuple(projects))
    findings = detect(state, ActivityIndex(), _defaults(), now=NOW)
    ptg = [f for f in findings if f.type == "scope_mismatch_project_to_global"]
    assert len(ptg) == 1
    f = ptg[0]
    assert f.scope.kind == "project_to_global"
    assert f.auto_checked is False
    assert f.action.primitive == "move_claude_md_section"


def test_project_to_global_skipped_when_global_has_same_section(tmp_path: Path) -> None:
    shared = "# Shared rule\nnever commit to main\n"
    projects: list[Path] = []
    for name in ("alpha", "bravo", "charlie"):
        project = tmp_path / name
        project.mkdir()
        (project / "CLAUDE.md").write_text(shared, encoding="utf-8")
        projects.append(project)
    state = _state(
        claude_home=tmp_path / ".claude",
        global_md=shared,
        projects=tuple(projects),
    )
    findings = detect(state, ActivityIndex(), _defaults(), now=NOW)
    assert all(f.type != "scope_mismatch_project_to_global" for f in findings)


def test_project_to_global_respects_promote_min_projects_threshold(tmp_path: Path) -> None:
    shared = "# Shared rule\nnever commit to main\n"
    projects: list[Path] = []
    for name in ("alpha", "bravo"):  # only 2 < default threshold of 3
        project = tmp_path / name
        project.mkdir()
        (project / "CLAUDE.md").write_text(shared, encoding="utf-8")
        projects.append(project)
    state = _state(claude_home=tmp_path / ".claude", projects=tuple(projects))
    findings = detect(state, ActivityIndex(), _defaults(), now=NOW)
    assert all(f.type != "scope_mismatch_project_to_global" for f in findings)


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
