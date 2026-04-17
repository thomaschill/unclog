"""Minimal markdown section-tree parser for CLAUDE.md lint passes.

v0.1 scope is deliberately small: parse ATX-style (``#``..``######``)
headings into a flat list of :class:`Section` records that carry enough
location metadata for the apply layer (M5) to locate and strip sections
deterministically. Setext headings, HTML blocks, indented code blocks,
and link-reference definitions are treated as body text.

The parser is aware of fenced code blocks (``` ``` ``` `` and ``~~~``)
so that ``#`` lines inside code don't get interpreted as headings —
CLAUDE.md files routinely include shell snippets and Python docstring
examples that would otherwise produce spurious sections.

Content that appears before the first heading is captured as a single
preamble section with ``heading_level = 0`` and ``heading_text = ""``.
Empty input returns an empty list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ATX_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")


@dataclass(frozen=True)
class Section:
    """A contiguous slice of a markdown document keyed by heading path.

    ``heading_level`` is ``0`` for preamble content before the first
    heading, otherwise the ATX level of the heading that opens the
    section (``1`` for ``#``, ``6`` for ``######``).

    ``heading_path`` is the chain of heading texts from level 1 down to
    this section's heading, inclusive. For preamble it is empty. Siblings
    at a given level do not appear in each other's paths.

    ``body`` is the raw text of the section starting with the heading
    line (or the first line of the document, for preamble) and ending
    just before the next heading of any level — or end-of-file.

    Line numbers are 1-based, inclusive on both ends. ``byte_offset`` is
    the UTF-8 offset of the first character of ``body`` in the original
    document; ``byte_length`` is ``len(body.encode("utf-8"))``.
    """

    heading_level: int
    heading_text: str
    heading_path: tuple[str, ...]
    body: str
    start_line: int
    end_line: int
    byte_offset: int
    byte_length: int


def parse_sections(text: str) -> list[Section]:
    """Parse ``text`` into a flat, document-order list of sections."""
    if not text:
        return []

    lines = text.splitlines(keepends=True)
    # Precompute UTF-8 byte offset of each line start so section byte
    # offsets are exact even when the document contains multibyte chars.
    line_byte_offsets: list[int] = []
    running = 0
    for line in lines:
        line_byte_offsets.append(running)
        running += len(line.encode("utf-8"))
    total_bytes = running

    headings: list[tuple[int, int, str]] = []  # (line_index, level, text)
    in_fence = False
    fence_marker = ""
    for idx, raw in enumerate(lines):
        stripped = raw.rstrip("\n").rstrip("\r")
        fence_match = _FENCE_RE.match(stripped.lstrip())
        if fence_match:
            marker = fence_match.group(1)[0] * 3
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker and stripped.lstrip().startswith(fence_marker):
                in_fence = False
                fence_marker = ""
            continue
        if in_fence:
            continue
        heading_match = _ATX_HEADING_RE.match(stripped)
        if heading_match is None:
            continue
        level = len(heading_match.group(1))
        heading_text = heading_match.group(2).strip()
        headings.append((idx, level, heading_text))

    sections: list[Section] = []
    path_stack: list[tuple[int, str]] = []  # (level, text)

    def _body_slice(start_line_idx: int, end_line_idx_exclusive: int) -> tuple[str, int, int]:
        body = "".join(lines[start_line_idx:end_line_idx_exclusive])
        byte_offset = line_byte_offsets[start_line_idx]
        if end_line_idx_exclusive < len(lines):
            byte_end = line_byte_offsets[end_line_idx_exclusive]
        else:
            byte_end = total_bytes
        return body, byte_offset, byte_end - byte_offset

    preamble_end = headings[0][0] if headings else len(lines)
    if preamble_end > 0:
        body, offset, length = _body_slice(0, preamble_end)
        sections.append(
            Section(
                heading_level=0,
                heading_text="",
                heading_path=(),
                body=body,
                start_line=1,
                end_line=preamble_end,
                byte_offset=offset,
                byte_length=length,
            )
        )

    for pos, (line_idx, level, text_) in enumerate(headings):
        while path_stack and path_stack[-1][0] >= level:
            path_stack.pop()
        path_stack.append((level, text_))
        next_line_idx = headings[pos + 1][0] if pos + 1 < len(headings) else len(lines)
        body, offset, length = _body_slice(line_idx, next_line_idx)
        sections.append(
            Section(
                heading_level=level,
                heading_text=text_,
                heading_path=tuple(name for _, name in path_stack),
                body=body,
                start_line=line_idx + 1,
                end_line=next_line_idx,
                byte_offset=offset,
                byte_length=length,
            )
        )

    return sections
