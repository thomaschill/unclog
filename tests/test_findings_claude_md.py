from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

import pytest

from unclog.apply.primitives import apply_action
from unclog.apply.snapshot import create_snapshot
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


def test_global_to_project_finding_applies_cleanly(tmp_path: Path) -> None:
    """End-to-end: the finding produced by detect() must satisfy the primitive."""
    project = tmp_path / "draper"
    (project / "src").mkdir(parents=True)
    (project / "src" / "foo.py").write_text("", encoding="utf-8")
    claude_home = tmp_path / ".claude"
    global_md = (
        "# Keep me\nstays put\n"
        "# Draper-only\n"
        f"- look at `{project}/src/foo.py`\n"
    )
    state = _state(
        claude_home=claude_home,
        global_md=global_md,
        projects=(project,),
        config_projects=(project,),
    )
    findings = detect(state, ActivityIndex(), _defaults(), now=NOW)
    gtp = next(f for f in findings if f.type == "scope_mismatch_global_to_project")

    snap = create_snapshot(
        tmp_path / "snapshots",
        claude_home=claude_home,
        project_paths=(project,),
        now=NOW,
    )
    apply_action(gtp, snap, claude_home=claude_home)

    assert "Draper-only" not in (claude_home / "CLAUDE.md").read_text(encoding="utf-8")
    dest_text = (project / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Draper-only" in dest_text


def test_project_to_global_finding_applies_cleanly(tmp_path: Path) -> None:
    """Regression: promote-to-global finding used to omit destination_path."""
    shared = "# Shared rule\nnever commit to main\n"
    projects: list[Path] = []
    for name in ("alpha", "bravo", "charlie"):
        project = tmp_path / name
        project.mkdir()
        (project / "CLAUDE.md").write_text(shared, encoding="utf-8")
        projects.append(project)
    claude_home = tmp_path / ".claude"
    state = _state(claude_home=claude_home, projects=tuple(projects))
    findings = detect(state, ActivityIndex(), _defaults(), now=NOW)
    ptg = next(f for f in findings if f.type == "scope_mismatch_project_to_global")

    snap = create_snapshot(
        tmp_path / "snapshots",
        claude_home=claude_home,
        project_paths=tuple(projects),
        now=NOW,
    )
    # Must not raise ApplyError("missing evidence.destination_path").
    apply_action(ptg, snap, claude_home=claude_home)

    # Section lands in global CLAUDE.md and is stripped from the canonical source.
    assert "Shared rule" in (claude_home / "CLAUDE.md").read_text(encoding="utf-8")
    canonical_source = Path(ptg.evidence["source_path"])
    assert "Shared rule" not in canonical_source.read_text(encoding="utf-8")


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
