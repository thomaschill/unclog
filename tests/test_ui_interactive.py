from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from rich.console import Console

from unclog.findings.base import Action, Finding, Scope
from unclog.ui.interactive import RichPrompter, run_interactive
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
        *,
        invocation_view: object | None = None,
    ) -> list[Finding]:
        self.multiselect_calls.append((title, sections))
        return list(self.multiselect_answer)


def _curate(path: Path, ftype: str = "agent_inventory", tokens: int = 42) -> Finding:
    return Finding(
        id=f"{ftype}:{path.stem}",
        type=ftype,  # type: ignore[arg-type]
        title=f"curate {path.stem}",
        scope=Scope(kind="global"),
        action=Action(primitive="delete_file", path=path),
        token_savings=tokens,
    )


def test_run_interactive_returns_none_when_no_findings(tmp_path: Path) -> None:
    result = run_interactive(
        [],
        claude_home=tmp_path,
        console=Console(record=True),
        baseline_tokens=0,
        prompter=FakePrompter(),
    )
    assert result is None


def test_empty_picker_selection_bypasses_apply(tmp_path: Path) -> None:
    agent_md = tmp_path / "agents" / "foo.md"
    agent_md.parent.mkdir(parents=True)
    agent_md.write_text("a\n", encoding="utf-8")
    curate = [_curate(agent_md)]
    prompter = FakePrompter(multiselect_answer=[])
    result = run_interactive(
        curate,
        claude_home=tmp_path,
        console=Console(record=True),
        baseline_tokens=1000,
        prompter=prompter,
    )
    assert result is None
    assert agent_md.exists()
    # Picker is the first decision — no confirm fires until something is selected.
    assert prompter.confirm_calls == []


def test_confirm_no_bypasses_apply(tmp_path: Path) -> None:
    agent_md = tmp_path / "agents" / "foo.md"
    agent_md.parent.mkdir(parents=True)
    agent_md.write_text("a\n", encoding="utf-8")
    curate = [_curate(agent_md)]
    prompter = FakePrompter(confirm_answers=[False], multiselect_answer=curate)
    result = run_interactive(
        curate,
        claude_home=tmp_path,
        console=Console(record=True),
        baseline_tokens=1000,
        prompter=prompter,
    )
    assert result is None
    assert agent_md.exists()


def test_selection_confirms_and_applies(tmp_path: Path) -> None:
    agent_md = tmp_path / "agents" / "foo.md"
    agent_md.parent.mkdir(parents=True)
    agent_md.write_text("a\n", encoding="utf-8")
    curate = [_curate(agent_md)]
    prompter = FakePrompter(confirm_answers=[True], multiselect_answer=curate)
    result = run_interactive(
        curate,
        claude_home=tmp_path,
        console=Console(record=True),
        baseline_tokens=1000,
        prompter=prompter,
    )
    assert result is not None
    assert len(result.succeeded) == 1
    assert not agent_md.exists()
    assert prompter.confirm_calls[0].startswith("Delete 1 item")


def test_post_apply_renders_baseline_line(tmp_path: Path) -> None:
    agent_md = tmp_path / "agents" / "foo.md"
    agent_md.parent.mkdir(parents=True)
    agent_md.write_text("a\n", encoding="utf-8")
    curate = [_curate(agent_md, tokens=500)]
    console = Console(record=True, width=120)
    prompter = FakePrompter(confirm_answers=[True], multiselect_answer=curate)
    result = run_interactive(
        curate,
        claude_home=tmp_path,
        console=console,
        baseline_tokens=42_000,
        prompter=prompter,
    )
    assert result is not None
    output = console.export_text()
    assert "saved" in output
    assert "500" in output
    assert "41,500" in output


def test_picker_partitions_by_type(tmp_path: Path) -> None:
    agent_md = tmp_path / "agents" / "foo.md"
    agent_md.parent.mkdir(parents=True)
    agent_md.write_text("a\n", encoding="utf-8")
    skill_md = tmp_path / "skills" / "bar" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("s\n", encoding="utf-8")
    command_md = tmp_path / "commands" / "ship.md"
    command_md.parent.mkdir(parents=True)
    command_md.write_text("c\n", encoding="utf-8")
    curate = [
        _curate(agent_md, ftype="agent_inventory"),
        _curate(skill_md, ftype="skill_inventory"),
        _curate(command_md, ftype="command_inventory"),
    ]
    prompter = FakePrompter(multiselect_answer=[])
    run_interactive(
        curate,
        claude_home=tmp_path,
        console=Console(record=True),
        baseline_tokens=1000,
        prompter=prompter,
    )
    _, sections = prompter.multiselect_calls[0]
    assert [s.title for s in sections] == [
        "Curate agents",
        "Curate skills",
        "Curate commands",
    ]


def test_single_type_picker_suppresses_section_title(tmp_path: Path) -> None:
    agent_md = tmp_path / "agents" / "foo.md"
    agent_md.parent.mkdir(parents=True)
    agent_md.write_text("a\n", encoding="utf-8")
    curate = [_curate(agent_md)]
    prompter = FakePrompter(multiselect_answer=[])
    run_interactive(
        curate,
        claude_home=tmp_path,
        console=Console(record=True),
        baseline_tokens=1000,
        prompter=prompter,
    )
    _, sections = prompter.multiselect_calls[0]
    assert len(sections) == 1
    assert sections[0].title == ""


# -- star callout -----------------------------------------------------------


def test_star_line_shows_once_then_is_suppressed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Post-apply star line renders on first success, never again."""
    from unclog.util.paths import claude_home as _claude_home

    claude_root = tmp_path / ".claude"
    claude_root.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_root))
    _claude_home.cache_clear()

    def _run_once(file_name: str) -> str:
        agent_md = tmp_path / "agents" / file_name
        agent_md.parent.mkdir(parents=True, exist_ok=True)
        agent_md.write_text("a\n", encoding="utf-8")
        curate = [_curate(agent_md, tokens=100)]
        console = Console(record=True, width=120)
        prompter = FakePrompter(confirm_answers=[True], multiselect_answer=curate)
        run_interactive(
            curate,
            claude_home=claude_root,
            console=console,
            baseline_tokens=1000,
            prompter=prompter,
        )
        return console.export_text()

    first_output = _run_once("first.md")
    assert "Star it on GitHub" in first_output
    assert (claude_root / ".unclog" / "star_shown").exists()

    second_output = _run_once("second.md")
    assert "Star it on GitHub" not in second_output


# -- RichPrompter.confirm semantics -----------------------------------------


def test_rich_prompter_confirm_reraises_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ctrl+C at the confirm prompt must propagate, not silently become No."""
    import builtins

    def _raise_kbi(_prompt: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(builtins, "input", _raise_kbi)
    prompter = RichPrompter(Console(record=True))
    with pytest.raises(KeyboardInterrupt):
        prompter.confirm("Apply?", default=False)


def test_rich_prompter_confirm_returns_false_on_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EOFError (pipe closed / no TTY) defaults to No, no traceback."""
    import builtins

    def _raise_eof(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr(builtins, "input", _raise_eof)
    prompter = RichPrompter(Console(record=True))
    assert prompter.confirm("Apply?", default=True) is False
    assert prompter.confirm("Apply?", default=False) is False
