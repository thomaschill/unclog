from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from rich.console import Console

from unclog.findings.base import Action, Finding, Scope
from unclog.ui.interactive import InteractiveOptions, run_interactive


@dataclass
class FakePrompter:
    """In-memory prompter for deterministic test runs."""

    confirm_answers: list[bool] = field(default_factory=list)
    multiselect_answer: list[Finding] = field(default_factory=list)
    confirm_calls: list[str] = field(default_factory=list)

    def confirm(self, message: str, default: bool) -> bool:
        self.confirm_calls.append(message)
        if not self.confirm_answers:
            return default
        return self.confirm_answers.pop(0)

    def multiselect(
        self, message: str, choices: list[tuple[str, Finding]], defaults: set[str]
    ) -> list[Finding]:
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


def test_interactive_first_no_bypasses_apply(tmp_path: Path) -> None:
    skill_md = tmp_path / "skills" / "g" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("body\n", encoding="utf-8")
    findings = [_f("a", "delete_file", path=skill_md)]
    prompter = FakePrompter(confirm_answers=[False])
    result = run_interactive(
        findings,
        claude_home=tmp_path,
        project_paths=(),
        console=Console(record=True),
        options=InteractiveOptions(),
        prompter=prompter,
    )
    assert result is None
    assert skill_md.exists()  # no mutation happened


def test_interactive_second_no_bypasses_apply(tmp_path: Path) -> None:
    skill_md = tmp_path / "skills" / "g" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("body\n", encoding="utf-8")
    finding = _f("a", "delete_file", path=skill_md)
    prompter = FakePrompter(
        confirm_answers=[True, False],  # fix these? yes. apply N? no.
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
        confirm_answers=[True, True],
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


def test_interactive_dry_run_skips_apply(tmp_path: Path) -> None:
    skill_md = tmp_path / "skills" / "g" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("body\n", encoding="utf-8")
    finding = _f("a", "delete_file", path=skill_md)
    prompter = FakePrompter(
        confirm_answers=[True, True],
        multiselect_answer=[finding],
    )
    result = run_interactive(
        [finding],
        claude_home=tmp_path,
        project_paths=(),
        console=Console(record=True),
        options=InteractiveOptions(dry_run=True),
        prompter=prompter,
    )
    assert result is None
    assert skill_md.exists()  # dry-run: file untouched


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
