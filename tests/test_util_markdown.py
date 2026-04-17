from __future__ import annotations

from unclog.util.markdown import parse_sections


def test_empty_text_has_no_sections() -> None:
    assert parse_sections("") == []


def test_preamble_only_gets_level_zero_section() -> None:
    text = "intro line one\nintro line two\n"
    sections = parse_sections(text)
    assert len(sections) == 1
    preamble = sections[0]
    assert preamble.heading_level == 0
    assert preamble.heading_text == ""
    assert preamble.heading_path == ()
    assert preamble.body == text
    assert preamble.start_line == 1
    assert preamble.end_line == 2
    assert preamble.byte_offset == 0
    assert preamble.byte_length == len(text.encode("utf-8"))


def test_single_top_level_heading() -> None:
    text = "# Top\nbody line\n"
    sections = parse_sections(text)
    assert len(sections) == 1
    top = sections[0]
    assert top.heading_level == 1
    assert top.heading_text == "Top"
    assert top.heading_path == ("Top",)
    assert top.body == text
    assert top.start_line == 1
    assert top.end_line == 2


def test_nested_headings_build_heading_path() -> None:
    text = (
        "# Alpha\n"
        "a body\n"
        "## Alpha.One\n"
        "a1 body\n"
        "### Alpha.One.A\n"
        "a1a body\n"
        "## Alpha.Two\n"
        "a2 body\n"
        "# Beta\n"
        "b body\n"
    )
    sections = parse_sections(text)
    paths = [s.heading_path for s in sections]
    assert paths == [
        ("Alpha",),
        ("Alpha", "Alpha.One"),
        ("Alpha", "Alpha.One", "Alpha.One.A"),
        ("Alpha", "Alpha.Two"),
        ("Beta",),
    ]
    # Sibling pop: Alpha.Two should not contain Alpha.One in its path.
    assert sections[3].heading_path[-2] == "Alpha"
    # End-of-section extends to the next heading of any level.
    assert sections[0].body.startswith("# Alpha\n")
    assert sections[0].body.endswith("a body\n")
    assert "Alpha.One" not in sections[0].body


def test_preamble_and_headings_coexist() -> None:
    text = "preamble\n\n# First\nbody\n"
    sections = parse_sections(text)
    assert [s.heading_level for s in sections] == [0, 1]
    assert sections[0].body == "preamble\n\n"
    assert sections[1].body == "# First\nbody\n"


def test_fenced_code_block_suppresses_heading_detection() -> None:
    text = (
        "# Real\n"
        "```bash\n"
        "# not a heading\n"
        "## also not a heading\n"
        "```\n"
        "## Real Sub\n"
        "body\n"
    )
    sections = parse_sections(text)
    assert [s.heading_text for s in sections] == ["Real", "Real Sub"]


def test_tilde_fence_also_suppresses_headings() -> None:
    text = "# A\n~~~\n# fake\n~~~\n## B\n"
    sections = parse_sections(text)
    assert [s.heading_text for s in sections] == ["A", "B"]


def test_trailing_hashes_on_heading_are_stripped() -> None:
    text = "# Title ##\nbody\n"
    sections = parse_sections(text)
    assert sections[0].heading_text == "Title"


def test_heading_requires_space_after_hashes() -> None:
    text = "#notaheading\nplain line\n# Real\n"
    sections = parse_sections(text)
    assert [s.heading_level for s in sections] == [0, 1]
    assert sections[0].body == "#notaheading\nplain line\n"


def test_byte_offsets_match_utf8_content() -> None:
    text = "# H1\nbody 🙂 more\n## H2\nend\n"
    sections = parse_sections(text)
    encoded = text.encode("utf-8")
    for section in sections:
        chunk = encoded[section.byte_offset : section.byte_offset + section.byte_length]
        assert chunk.decode("utf-8") == section.body


def test_line_ranges_cover_every_line_exactly_once() -> None:
    text = "preamble\n# A\nx\n# B\ny\nz\n"
    sections = parse_sections(text)
    covered: list[int] = []
    for section in sections:
        covered.extend(range(section.start_line, section.end_line + 1))
    assert covered == list(range(1, len(text.splitlines()) + 1))


def test_document_without_trailing_newline() -> None:
    text = "# Tight\nno trailing newline"
    sections = parse_sections(text)
    assert len(sections) == 1
    assert sections[0].body == text
    assert sections[0].byte_length == len(text.encode("utf-8"))
