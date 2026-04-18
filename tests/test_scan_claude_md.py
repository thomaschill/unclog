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


def test_at_import_live_target_tracked(tmp_path: Path) -> None:
    (tmp_path / "child.md").write_text("# Child\n", encoding="utf-8")
    path = tmp_path / "CLAUDE.md"
    text = "See @child.md for details\n"
    parsed = parse_claude_md(path, text, _StubCounter())
    # Live import shows up in live_imports, not dead_refs.
    assert parsed.dead_refs == ()
    assert len(parsed.live_imports) == 1
    assert parsed.live_imports[0].name == "child.md"


def test_at_import_missing_target_flagged(tmp_path: Path) -> None:
    path = tmp_path / "CLAUDE.md"
    text = "@missing.md\n"
    parsed = parse_claude_md(path, text, _StubCounter())
    assert len(parsed.dead_refs) == 1
    ref = parsed.dead_refs[0]
    assert ref.raw == "@missing.md"
    assert ref.import_depth == 1
    assert ref.import_parent == path
    assert ref.line_only is True


def test_at_import_transitive_dead_captures_parent(tmp_path: Path) -> None:
    (tmp_path / "mid.md").write_text("@./nope.md\n", encoding="utf-8")
    path = tmp_path / "CLAUDE.md"
    text = "@./mid.md\n"
    parsed = parse_claude_md(path, text, _StubCounter())
    refs_by_depth = {r.import_depth: r for r in parsed.dead_refs}
    assert 2 in refs_by_depth
    transitive = refs_by_depth[2]
    assert transitive.import_parent == tmp_path / "mid.md"
    assert transitive.line_only is False  # intermediate file — needs editor


def test_at_import_chain_caps_at_depth_five(tmp_path: Path) -> None:
    # Chain: root → d1 → d2 → d3 → d4 → d5 → dead.md
    # _MAX_IMPORT_DEPTH=5 means we stop BEFORE visiting d5's imports,
    # so dead.md (at depth 6) should not appear.
    for i in range(1, 6):
        (tmp_path / f"d{i}.md").write_text(
            f"@./d{i + 1}.md\n" if i < 5 else "@./dead.md\n",
            encoding="utf-8",
        )
    path = tmp_path / "CLAUDE.md"
    text = "@./d1.md\n"
    parsed = parse_claude_md(path, text, _StubCounter())
    # d5.md's @-import would resolve to depth 6 — past the cap.
    assert all(r.resolved.name != "dead.md" for r in parsed.dead_refs)


def test_at_import_cycle_does_not_infinite_loop(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("@./b.md\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("@./a.md\n", encoding="utf-8")
    path = tmp_path / "CLAUDE.md"
    text = "@./a.md\n"
    # Must terminate; visited-set prevents re-scanning a or b.
    parsed = parse_claude_md(path, text, _StubCounter())
    assert parsed.dead_refs == ()
    # Both files are reached once.
    names = {p.name for p in parsed.live_imports}
    assert names == {"a.md", "b.md"}


def test_at_import_ignored_inside_code_fence(tmp_path: Path) -> None:
    path = tmp_path / "CLAUDE.md"
    text = "```\n@missing.md\n```\n"
    parsed = parse_claude_md(path, text, _StubCounter())
    assert parsed.dead_refs == ()


def test_at_username_not_treated_as_import(tmp_path: Path) -> None:
    """@anthropic or email-like tokens must not match the import regex."""
    path = tmp_path / "CLAUDE.md"
    text = "thanks to @anthropic and user@example.com for support\n"
    parsed = parse_claude_md(path, text, _StubCounter())
    assert parsed.dead_refs == ()
    assert parsed.live_imports == ()
