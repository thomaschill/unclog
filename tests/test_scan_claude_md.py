from __future__ import annotations

from pathlib import Path

import pytest

from unclog.scan.claude_md import parse_claude_md


class _StubCounter:
    """Deterministic token counter for tests (4 chars == 1 token)."""

    def count(self, text: str) -> int:
        return max(0, len(text) // 4)


def test_empty_file_yields_empty_structure(tmp_path: Path) -> None:
    path = tmp_path / "CLAUDE.md"
    parsed = parse_claude_md(path, "", _StubCounter())
    assert parsed.sections == ()
    assert parsed.dead_refs == ()


def test_sections_carry_tokens_and_body_hash(tmp_path: Path) -> None:
    path = tmp_path / "CLAUDE.md"
    text = "# One\nhello world\n# Two\nanother body\n"
    parsed = parse_claude_md(path, text, _StubCounter())
    assert len(parsed.sections) == 2
    one = parsed.sections[0]
    two = parsed.sections[1]
    assert one.section.heading_text == "One"
    assert one.tokens > 0
    # Different bodies hash differently.
    assert one.body_hash != two.body_hash


def test_exact_duplicate_sections_share_body_hash(tmp_path: Path) -> None:
    path = tmp_path / "CLAUDE.md"
    text = "# A\nuse yarn always\n# B\nuse yarn always\n"
    parsed = parse_claude_md(path, text, _StubCounter())
    a, b = parsed.sections
    assert a.body_hash == b.body_hash


def test_body_hash_ignores_surrounding_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "CLAUDE.md"
    one = "# A\nuse yarn\n"
    two = "# A\n\nuse yarn\n\n"
    h1 = parse_claude_md(path, one, _StubCounter()).sections[0].body_hash
    h2 = parse_claude_md(path, two, _StubCounter()).sections[0].body_hash
    assert h1 == h2


def test_absolute_dead_ref_flagged(tmp_path: Path) -> None:
    path = tmp_path / "CLAUDE.md"
    text = "refer to `/definitely/not/a/real/path/x.py` for details\n"
    parsed = parse_claude_md(path, text, _StubCounter())
    assert len(parsed.dead_refs) == 1
    ref = parsed.dead_refs[0]
    assert ref.raw == "/definitely/not/a/real/path/x.py"
    assert ref.line_number == 1
    assert ref.line_only is False


def test_relative_dead_ref_resolves_against_file_directory(tmp_path: Path) -> None:
    path = tmp_path / "CLAUDE.md"
    # ./ghost.txt resolves next to the CLAUDE.md, which doesn't exist.
    text = "- ./ghost.txt\n"
    parsed = parse_claude_md(path, text, _StubCounter())
    assert len(parsed.dead_refs) == 1
    ref = parsed.dead_refs[0]
    assert ref.line_only is True


def test_existing_relative_ref_not_flagged(tmp_path: Path) -> None:
    (tmp_path / "exists.txt").write_text("hi", encoding="utf-8")
    path = tmp_path / "CLAUDE.md"
    text = "- `./exists.txt`\n"
    parsed = parse_claude_md(path, text, _StubCounter())
    assert parsed.dead_refs == ()


def test_line_only_distinction(tmp_path: Path) -> None:
    path = tmp_path / "CLAUDE.md"
    text = (
        "- /nope/one.py\n"  # line-only bullet, strippable
        "see /nope/two.py for details\n"  # mixed with prose
    )
    parsed = parse_claude_md(path, text, _StubCounter())
    refs = {r.raw: r for r in parsed.dead_refs}
    assert refs["/nope/one.py"].line_only is True
    assert refs["/nope/two.py"].line_only is False


def test_trailing_punctuation_is_stripped_before_stat(tmp_path: Path) -> None:
    path = tmp_path / "CLAUDE.md"
    text = "see /nope/file.py.\n"
    parsed = parse_claude_md(path, text, _StubCounter())
    assert len(parsed.dead_refs) == 1
    assert parsed.dead_refs[0].raw == "/nope/file.py"


def test_urls_are_not_treated_as_paths(tmp_path: Path) -> None:
    path = tmp_path / "CLAUDE.md"
    text = "see https://example.com/docs for notes\n"
    parsed = parse_claude_md(path, text, _StubCounter())
    assert parsed.dead_refs == ()


def test_dead_refs_inside_code_fence_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "CLAUDE.md"
    text = (
        "real content\n"
        "```\n"
        "/nope/inside-fence.py\n"
        "```\n"
        "/nope/outside-fence.py\n"
    )
    parsed = parse_claude_md(path, text, _StubCounter())
    assert len(parsed.dead_refs) == 1
    assert parsed.dead_refs[0].raw == "/nope/outside-fence.py"


def test_bare_relative_without_prefix_is_not_matched(tmp_path: Path) -> None:
    # v0.1 intentionally ignores bare "src/foo.py" to avoid prose false positives.
    path = tmp_path / "CLAUDE.md"
    text = "we use src/nope.py sometimes\n"
    parsed = parse_claude_md(path, text, _StubCounter())
    assert parsed.dead_refs == ()


def test_home_ref_is_expanded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Point HOME somewhere empty so ~/ghost can't accidentally exist.
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()
    monkeypatch.setenv("HOME", str(empty_home))
    path = tmp_path / "CLAUDE.md"
    text = "- ~/ghost.md\n"
    parsed = parse_claude_md(path, text, _StubCounter())
    assert len(parsed.dead_refs) == 1
    assert parsed.dead_refs[0].resolved == empty_home / "ghost.md"
