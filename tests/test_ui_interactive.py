from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from rich.console import Console

from unclog.findings.base import Action, Finding, Scope
from unclog.ui.interactive import InteractiveOptions, run_interactive
from unclog.ui.picker import Section


@dataclass
class FakePrompter:
    """In-memory prompter for deterministic test runs."""

    confirm_answers: list[bool] = field(default_factory=list)
    multiselect_answer: list[Finding] = field(default_factory=list)
    confirm_calls: list[str] = field(default_factory=list)
    multiselect_calls: list[tuple[str, list[Section]]] = field(default_factory=list)

    def confirm(self, message: str, default: bool) -> bool:
        self.confirm_calls.append(message)
        if not self.confirm_answers:
            return default
        return self.confirm_answers.pop(0)

    def multiselect_sections(
        self,
        title: str,
        sections: list[Section],
    ) -> list[Finding]:
        self.multiselect_calls.append((title, sections))
        return list(self.multiselect_answer)


def _f(fid: str, primitive: str, *, auto_checked: bool = False, path: Path | None = None) -> Finding:
    return Finding(
        id=fid,
        type="unused_skill",
        title=f"title {fid}",
        reason="r",
        scope=Scope(kind="global"),
        action=Action(primitive=primitive, path=path),  # type: ignore[arg-type]
        auto_checked=auto_checked,
        evidence=MappingProxyType({}),
    )


def test_interactive_exits_cleanly_when_no_findings(tmp_path: Path) -> None:
    console = Console(record=True)
    result = run_interactive(
        [],
        claude_home=tmp_path,
        project_paths=(),
        console=console,
        options=InteractiveOptions(),
        prompter=FakePrompter(),
    )
    assert result is None


def test_interactive_empty_picker_selection_bypasses_apply(tmp_path: Path) -> None:
    skill_md = tmp_path / "skills" / "g" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("body\n", encoding="utf-8")
    findings = [_f("a", "delete_file", path=skill_md)]
    # Picker returns [] (user quit or submitted with nothing selected).
    prompter = FakePrompter(multiselect_answer=[])
    result = run_interactive(
        findings,
        claude_home=tmp_path,
        project_paths=(),
        console=Console(record=True),
        options=InteractiveOptions(),
        prompter=prompter,
    )
    assert result is None
    assert skill_md.exists()
    # No confirm prompt should have fired — picker is the first decision.
    assert prompter.confirm_calls == []


def test_interactive_apply_confirm_no_bypasses_apply(tmp_path: Path) -> None:
    skill_md = tmp_path / "skills" / "g" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("body\n", encoding="utf-8")
    finding = _f("a", "delete_file", path=skill_md)
    prompter = FakePrompter(
        confirm_answers=[False],  # apply N? no.
        multiselect_answer=[finding],
    )
    result = run_interactive(
        [finding],
        claude_home=tmp_path,
        project_paths=(),
        console=Console(record=True),
        options=InteractiveOptions(),
        prompter=prompter,
    )
    assert result is None
    assert skill_md.exists()


def test_interactive_accepts_and_applies(tmp_path: Path) -> None:
    skill_md = tmp_path / "skills" / "g" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("body\n", encoding="utf-8")
    finding = _f("a", "delete_file", path=skill_md)
    prompter = FakePrompter(
        confirm_answers=[True],
        multiselect_answer=[finding],
    )
    result = run_interactive(
        [finding],
        claude_home=tmp_path,
        project_paths=(),
        console=Console(record=True),
        options=InteractiveOptions(),
        prompter=prompter,
    )
    assert result is not None
    assert not skill_md.exists()
    assert len(result.succeeded) == 1


def test_interactive_yes_applies_only_auto_checked(tmp_path: Path) -> None:
    a_md = tmp_path / "skills" / "a" / "SKILL.md"
    a_md.parent.mkdir(parents=True)
    a_md.write_text("a\n", encoding="utf-8")
    b_md = tmp_path / "skills" / "b" / "SKILL.md"
    b_md.parent.mkdir(parents=True)
    b_md.write_text("b\n", encoding="utf-8")

    auto = _f("a", "delete_file", auto_checked=True, path=a_md)
    optin = _f("b", "delete_file", auto_checked=False, path=b_md)
    result = run_interactive(
        [auto, optin],
        claude_home=tmp_path,
        project_paths=(),
        console=Console(record=True),
        options=InteractiveOptions(yes=True),
        prompter=FakePrompter(),
    )
    assert result is not None
    assert not a_md.exists()
    assert b_md.exists()


def test_interactive_countdown_runs_when_baseline_provided(tmp_path: Path) -> None:
    skill_md = tmp_path / "skills" / "g" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("body\n", encoding="utf-8")
    finding = Finding(
        id="x",
        type="unused_skill",
        title="title",
        reason="r",
        scope=Scope(kind="global"),
        action=Action(primitive="delete_file", path=skill_md),
        auto_checked=False,
        token_savings=500,
    )
    prompter = FakePrompter(
        confirm_answers=[True],
        multiselect_answer=[finding],
    )
    console = Console(record=True)
    result = run_interactive(
        [finding],
        claude_home=tmp_path,
        project_paths=(),
        console=console,
        options=InteractiveOptions(no_animation=True),
        prompter=prompter,
        baseline_tokens=42_000,
    )
    assert result is not None
    output = console.export_text()
    # Static (animate=False) countdown prints before -> after on one line.
    assert "42,000" in output
    assert "41,500" in output


def _flag_only(fid: str, ftype: str, **extra: object) -> Finding:
    return Finding(
        id=fid,
        type=ftype,  # type: ignore[arg-type]
        title=f"title {fid}",
        reason="r",
        scope=Scope(kind="global"),
        action=Action(primitive="flag_only", **extra),  # type: ignore[arg-type]
        auto_checked=False,
        evidence=MappingProxyType({}),
    )


def test_interactive_flag_only_findings_render_next_step_hints(tmp_path: Path) -> None:
    console = Console(record=True, width=120)
    prompter = FakePrompter()
    findings = [
        _flag_only("missing_claudeignore:/tmp/a", "missing_claudeignore",
                   path=tmp_path / "a" / ".claudeignore"),
        _flag_only("disabled_plugin_residue:foo@mp", "disabled_plugin_residue",
                   plugin_key="foo@mp"),
    ]
    result = run_interactive(
        findings,
        claude_home=tmp_path,
        project_paths=(),
        console=console,
        options=InteractiveOptions(),
        prompter=prompter,
    )
    assert result is None
    # No prompts — flag-only findings render hints and exit.
    assert prompter.confirm_calls == []
    output = console.export_text()
    assert "No auto-fixable issues" in output
    # Each finding type gets a concrete manual-remediation hint.
    assert "create" in output  # missing_claudeignore hint
    assert "leave in place" in output  # disabled_plugin_residue hint


def test_interactive_yes_with_no_auto_checked_is_noop(tmp_path: Path) -> None:
    b_md = tmp_path / "skills" / "b" / "SKILL.md"
    b_md.parent.mkdir(parents=True)
    b_md.write_text("b\n", encoding="utf-8")
    finding = _f("b", "delete_file", auto_checked=False, path=b_md)
    result = run_interactive(
        [finding],
        claude_home=tmp_path,
        project_paths=(),
        console=Console(record=True),
        options=InteractiveOptions(yes=True),
        prompter=FakePrompter(),
    )
    assert result is None
    assert b_md.exists()


def _curate_finding(path: Path, ftype: str = "agent_inventory", tokens: int = 42) -> Finding:
    return Finding(
        id=f"{ftype}:{path.stem}",
        type=ftype,  # type: ignore[arg-type]
        title=f"curate {path.stem}",
        reason="r",
        scope=Scope(kind="global"),
        action=Action(primitive="delete_file", path=path),
        auto_checked=False,
        token_savings=tokens,
    )


def test_interactive_curate_only_picker_skips_when_nothing_selected(tmp_path: Path) -> None:
    """With curate-only and an empty picker selection, we exit without
    confirming or applying — single-picker flow has no separate y/N gate."""
    agent_md = tmp_path / "agents" / "foo.md"
    agent_md.parent.mkdir(parents=True)
    agent_md.write_text("a\n", encoding="utf-8")
    curate = [_curate_finding(agent_md)]
    prompter = FakePrompter(multiselect_answer=[])  # nothing picked
    result = run_interactive(
        [],
        claude_home=tmp_path,
        project_paths=(),
        console=Console(record=True),
        options=InteractiveOptions(),
        prompter=prompter,
        curate_findings=curate,
    )
    assert result is None
    assert agent_md.exists()
    assert prompter.confirm_calls == []
    # One picker call; single-section so title is suppressed.
    assert len(prompter.multiselect_calls) == 1
    title, sections = prompter.multiselect_calls[0]
    assert title == "Select fixes and curate"
    assert len(sections) == 1
    assert sections[0].title == ""


def test_interactive_curate_selection_confirms_and_applies(tmp_path: Path) -> None:
    """Single picker → single confirm → apply. No curate y/N preamble."""
    agent_md = tmp_path / "agents" / "foo.md"
    agent_md.parent.mkdir(parents=True)
    agent_md.write_text("a\n", encoding="utf-8")
    curate = [_curate_finding(agent_md)]
    prompter = FakePrompter(
        confirm_answers=[True],  # only the apply confirm
        multiselect_answer=curate,
    )
    result = run_interactive(
        [],
        claude_home=tmp_path,
        project_paths=(),
        console=Console(record=True),
        options=InteractiveOptions(),
        prompter=prompter,
        curate_findings=curate,
    )
    assert result is not None
    assert len(result.succeeded) == 1
    assert not agent_md.exists()
    assert len(prompter.confirm_calls) == 1
    assert prompter.confirm_calls[0].startswith("Apply 1 change")


def test_interactive_combined_picker_renders_apply_and_curate_sections(
    tmp_path: Path,
) -> None:
    """When both detector and curate findings exist, the picker gets
    multiple titled sections (Apply + Curate agents)."""
    apply_md = tmp_path / "skills" / "g" / "SKILL.md"
    apply_md.parent.mkdir(parents=True)
    apply_md.write_text("body\n", encoding="utf-8")
    agent_md = tmp_path / "agents" / "foo.md"
    agent_md.parent.mkdir(parents=True)
    agent_md.write_text("a\n", encoding="utf-8")
    applicable = [_f("a", "delete_file", path=apply_md)]
    curate = [_curate_finding(agent_md)]
    prompter = FakePrompter(multiselect_answer=[])
    run_interactive(
        applicable,
        claude_home=tmp_path,
        project_paths=(),
        console=Console(record=True),
        options=InteractiveOptions(),
        prompter=prompter,
        curate_findings=curate,
    )
    assert len(prompter.multiselect_calls) == 1
    _, sections = prompter.multiselect_calls[0]
    titles = [s.title for s in sections]
    assert titles == ["Apply", "Curate agents"]


def test_interactive_curate_picker_partitions_by_type(tmp_path: Path) -> None:
    """Mixed curate findings split into per-type sections; empty types drop."""
    agent_md = tmp_path / "agents" / "foo.md"
    agent_md.parent.mkdir(parents=True)
    agent_md.write_text("a\n", encoding="utf-8")
    skill_dir = tmp_path / "skills" / "bar"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("s\n", encoding="utf-8")
    curate = [
        _curate_finding(agent_md, ftype="agent_inventory"),
        _curate_finding(skill_dir / "SKILL.md", ftype="skill_inventory"),
    ]
    prompter = FakePrompter(multiselect_answer=[])
    run_interactive(
        [],
        claude_home=tmp_path,
        project_paths=(),
        console=Console(record=True),
        options=InteractiveOptions(),
        prompter=prompter,
        curate_findings=curate,
    )
    _, sections = prompter.multiselect_calls[0]
    titles = [s.title for s in sections]
    # Two non-empty types → titled sections; MCPs absent → dropped.
    assert titles == ["Curate agents", "Curate skills"]


def test_interactive_yes_mode_does_not_run_curate(tmp_path: Path) -> None:
    """--yes is for detector-driven auto-apply; curate is always hand-picked."""
    agent_md = tmp_path / "agents" / "foo.md"
    agent_md.parent.mkdir(parents=True)
    agent_md.write_text("a\n", encoding="utf-8")
    curate = [_curate_finding(agent_md)]
    prompter = FakePrompter()
    result = run_interactive(
        [],
        claude_home=tmp_path,
        project_paths=(),
        console=Console(record=True),
        options=InteractiveOptions(yes=True),
        prompter=prompter,
        curate_findings=curate,
    )
    assert result is None
    assert agent_md.exists()
    assert prompter.confirm_calls == []


def test_interactive_returns_none_when_no_findings_and_no_curate(tmp_path: Path) -> None:
    result = run_interactive(
        [],
        claude_home=tmp_path,
        project_paths=(),
        console=Console(record=True),
        options=InteractiveOptions(),
        prompter=FakePrompter(),
        curate_findings=[],
    )
    assert result is None


# -- RichPrompter.confirm semantics (Fix #8) --------------------------------


def test_rich_prompter_confirm_rereaises_keyboard_interrupt(monkeypatch: object) -> None:
    """Regression (Fix #8): Ctrl+C at the confirm prompt must propagate.

    The previous implementation caught ``KeyboardInterrupt`` alongside
    ``EOFError`` and silently returned False, degrading a user-initiated
    cancel into a "No" answer. The CLI's top-level handler renders a
    clean ``Cancelled.`` message only if the interrupt reaches it.
    """
    import builtins

    import pytest

    from unclog.ui.interactive import RichPrompter

    def _raise_kbi(_prompt: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(builtins, "input", _raise_kbi)  # type: ignore[attr-defined]
    prompter = RichPrompter(Console(record=True))
    with pytest.raises(KeyboardInterrupt):
        prompter.confirm("Apply?", default=False)


def test_rich_prompter_confirm_returns_false_on_eof(monkeypatch: object) -> None:
    """EOFError (pipe closed / no TTY) still defaults to No, no traceback."""
    import builtins

    from unclog.ui.interactive import RichPrompter

    def _raise_eof(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr(builtins, "input", _raise_eof)  # type: ignore[attr-defined]
    prompter = RichPrompter(Console(record=True))
    assert prompter.confirm("Apply?", default=True) is False
    assert prompter.confirm("Apply?", default=False) is False
