"""Parse a CLAUDE.md file into measured sections and dead file references.

This is the input layer for the ``claude_md_*`` detectors (spec §7):

- Section parsing turns the raw markdown into a list of
  :class:`MeasuredSection` records — each section's body carries a
  tiktoken count so ``claude_md_oversized`` and the duplicate/scope
  detectors can reason about real token cost, not bytes.
- Dead-ref extraction scans every non-code line for path-shaped strings,
  resolves them against the CLAUDE.md's own directory, and stats them.
  Dead refs on lines that contain *only* the ref become candidates for
  ``remove_claude_md_lines`` (auto-check safe); dead refs mixed with
  prose are surfaced as manual-edit flags via ``open_in_editor``.

Conservative path extraction is the whole ball game here. A false
positive (flagging "foo/bar" in prose as a file path) would erode trust
fast, so v0.1 only accepts:

- Paths inside backticks (``` `./x.py` ```, ``` `/Users/...` ```).
- Bare tokens explicitly prefixed with ``/``, ``~/``, ``./``, or ``../``.

We deliberately do *not* match "src/foo.py"-style relative tokens
outside backticks because ambient prose (package names, URLs, version
strings) produces too many false hits. If users want those lints they
can wrap the ref in backticks, which is already the convention.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from unclog.scan.tokens import TokenCounter
from unclog.util.markdown import Section, parse_sections

# Path inside a single-backtick code span. We grab whatever the span
# contains and then re-apply the same prefix heuristics the bare-path
# matcher uses; this keeps the "what counts as a path" rule in one place.
_BACKTICK_RE = re.compile(r"`([^`\n]+)`")

# Bare path tokens outside code spans. Matches absolute (``/foo``),
# home-relative (``~/foo``), or explicitly-relative (``./``/``../``)
# prefixes followed by at least one non-whitespace, non-backtick char.
# We stop at whitespace, closing punctuation (``,``/``;``/``)``/``]``),
# and sentence terminators so "see ~/x." doesn't stat "~/x.".
_BARE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_/:.])"  # don't start mid-identifier or after "://"
    r"(~/|\./|\.\./|/)"
    r"([^\s`,;\)\]<>\"']+)"
)

_TRAILING_PUNCT = ".,;:!?)]}"

# Claude Code `@path` imports: the `@` prefix transitively inlines the
# referenced file's contents into the assembled CLAUDE.md context. We
# only match shapes where the token looks unambiguously like a path to
# minimise false positives on things like `@username` or `@anthropic`:
#
# - `@/abs/path`, `@~/home/path`
# - `@./rel/path`, `@../rel/path`
# - `@dir/file` (contains a slash)
# - `@name.ext` (contains a file extension)
_IMPORT_RE = re.compile(
    r"(?<![A-Za-z0-9_@])@"
    r"(~/[^\s`,;\)\]<>\"']+|"
    r"\.\.?/[^\s`,;\)\]<>\"']+|"
    r"/[^\s`,;\)\]<>\"']+|"
    r"[A-Za-z0-9_\-][A-Za-z0-9_\-./]*(?:/[^\s`,;\)\]<>\"']+|\.[A-Za-z0-9]+))"
)

# Claude Code caps @-import chains at depth 5 — a ceiling matched here
# so dead-ref detection terminates on pathological cycles or deep trees
# without the scan getting quadratic.
_MAX_IMPORT_DEPTH = 5


@dataclass(frozen=True)
class MeasuredSection:
    """A :class:`Section` paired with its tiktoken count and a body hash."""

    section: Section
    tokens: int
    body_hash: str


@dataclass(frozen=True)
class DeadRef:
    """A path-shaped string in CLAUDE.md that does not exist on disk.

    ``raw`` is the matched token exactly as it appeared in the file
    (before ``~`` expansion or relative resolution). ``resolved`` is
    what we actually stat'd. ``line_only`` is true when the line
    consists of nothing but whitespace and the ref — those are safe to
    strip via ``remove_claude_md_lines`` without touching prose.

    ``import_depth`` is 0 for ordinary path refs found in the file
    itself. Depth 1 means "this file's @-import points at a missing
    target"; depth N>1 means a transitively-imported CLAUDE.md at depth
    N-1 @-imports a missing target. Only depth-0 and depth-1 dead refs
    can be auto-stripped from the root file — deeper chains require
    editing whichever intermediate file contains the broken @.
    """

    raw: str
    line_number: int
    line_text: str
    resolved: Path
    line_only: bool
    import_depth: int = 0
    import_parent: Path | None = None


@dataclass(frozen=True)
class ParsedClaudeMd:
    """Everything the CLAUDE.md detectors need for a single file.

    ``live_imports`` lists every existing file reached via the root's
    ``@path`` imports (transitively, depth ≤5). Callers can use this
    to account for transitive token cost that the raw file's own
    tiktoken count would miss.
    """

    path: Path
    text: str
    sections: tuple[MeasuredSection, ...]
    dead_refs: tuple[DeadRef, ...]
    live_imports: tuple[Path, ...] = ()


def _strip_trailing_punct(token: str) -> str:
    while token and token[-1] in _TRAILING_PUNCT:
        token = token[:-1]
    return token


def _resolve_candidate(raw: str, base_dir: Path) -> Path | None:
    token = _strip_trailing_punct(raw.strip())
    if not token:
        return None
    if token.startswith("~/"):
        try:
            return Path(token).expanduser()
        except (OSError, RuntimeError):
            return None
    if token.startswith("/"):
        return Path(token)
    if token.startswith(("./", "../")):
        return (base_dir / token).resolve(strict=False) if base_dir else None
    return None


def _iter_candidates(line: str) -> list[tuple[str, bool]]:
    """Yield ``(raw_token, from_backtick)`` candidates from a single line.

    Backtick spans are extracted first; their contents are then removed
    from the line so bare-path extraction doesn't double-count them.
    """
    candidates: list[tuple[str, bool]] = []
    residual = line
    for match in _BACKTICK_RE.finditer(line):
        candidates.append((match.group(1), True))
    residual = _BACKTICK_RE.sub(" ", residual)
    for match in _BARE_PATH_RE.finditer(residual):
        candidates.append((match.group(0), False))
    return candidates


def _line_is_only_ref(line: str, raw: str) -> bool:
    """True if ``line`` is whitespace + bullet + ``raw`` (optionally wrapped).

    Used to decide whether dead-ref cleanup is safe to auto-apply:
    line-only refs can be stripped verbatim; anything wrapped in prose
    requires human review.
    """
    stripped = line.strip()
    # Allow list bullets / quote markers before the ref.
    for bullet in ("- ", "* ", "+ ", "> "):
        if stripped.startswith(bullet):
            stripped = stripped[len(bullet) :].strip()
            break
    if stripped == raw or stripped == f"`{raw}`":
        return True
    # Also accept the ref with trailing punctuation only.
    if _strip_trailing_punct(stripped) in {raw, f"`{raw}`"}:
        return True
    return False


def iter_resolved_paths(text: str, base_dir: Path) -> list[Path]:
    """Return every path-shaped token in ``text`` resolved to a ``Path``.

    Unlike :func:`_find_dead_refs` this does not stat anything — callers
    use the returned paths for grouping and containment tests (e.g.
    "does every referenced path live under one project?"). Candidate
    extraction follows the same rules as dead-ref detection: backtick
    spans plus ``/``-, ``~/``-, ``./``-, ``../``-prefixed bare tokens,
    with fenced code blocks skipped.
    """
    resolved: list[Path] = []
    in_fence = False
    fence_marker = ""
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif stripped.startswith(fence_marker):
                in_fence = False
                fence_marker = ""
            continue
        if in_fence:
            continue
        for raw, _ in _iter_candidates(line):
            candidate = _resolve_candidate(raw, base_dir)
            if candidate is not None:
                resolved.append(candidate)
    return resolved


def _find_dead_refs(text: str, base_dir: Path) -> tuple[DeadRef, ...]:
    """Scan ``text`` line by line and return unresolved path references.

    Lines inside fenced code blocks (``` ``` / ``~~~``) are skipped in
    their entirety — code samples frequently reference files that only
    exist inside a hypothetical example and would otherwise trigger
    false positives.
    """
    lines = text.splitlines()
    refs: list[DeadRef] = []
    in_fence = False
    fence_marker = ""
    for idx, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif stripped.startswith(fence_marker):
                in_fence = False
                fence_marker = ""
            continue
        if in_fence:
            continue
        for raw, _ in _iter_candidates(line):
            resolved = _resolve_candidate(raw, base_dir)
            if resolved is None:
                continue
            try:
                exists = resolved.exists()
            except OSError:
                exists = True  # be safe on permission errors — don't report
            if exists:
                continue
            refs.append(
                DeadRef(
                    raw=_strip_trailing_punct(raw.strip()),
                    line_number=idx,
                    line_text=line,
                    resolved=resolved,
                    line_only=_line_is_only_ref(line, _strip_trailing_punct(raw.strip())),
                )
            )
    return tuple(refs)


def _body_hash(section: Section) -> str:
    """Content-addressable section hash for duplicate detection.

    Strips the heading line and surrounding whitespace so cosmetic
    re-writes (adding/removing blank lines, shifting heading levels)
    don't hide an otherwise exact duplicate.
    """
    body = section.body
    if section.heading_level > 0:
        # Drop the first line (the heading itself).
        _, _, body = body.partition("\n")
    normalised = "\n".join(line.rstrip() for line in body.strip().splitlines())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def _resolve_import(raw: str, base_dir: Path) -> Path | None:
    """Resolve a single ``@path`` import target relative to ``base_dir``.

    Differs from :func:`_resolve_candidate` in that we accept bare
    relative paths (``@docs/x.md``) — the ``@`` prefix is unambiguous
    enough that we don't need the safety net of requiring ``./``.
    """
    token = _strip_trailing_punct(raw.strip())
    if not token:
        return None
    if token.startswith("~/"):
        try:
            return Path(token).expanduser()
        except (OSError, RuntimeError):
            return None
    if token.startswith("/"):
        return Path(token)
    if base_dir is None:
        return None
    return (base_dir / token).resolve(strict=False)


def _iter_import_candidates(text: str) -> list[tuple[int, str]]:
    """Return ``(line_number, raw_path)`` for every ``@path`` in ``text``.

    Fenced code blocks are skipped so documentation of the import
    syntax ("write ``@docs/style.md``") doesn't register as a live
    import.
    """
    results: list[tuple[int, str]] = []
    in_fence = False
    fence_marker = ""
    for idx, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif stripped.startswith(fence_marker):
                in_fence = False
                fence_marker = ""
            continue
        if in_fence:
            continue
        for match in _IMPORT_RE.finditer(line):
            results.append((idx, match.group(1)))
    return results


def _walk_import_chain(
    root_path: Path,
    root_text: str,
    *,
    max_depth: int = _MAX_IMPORT_DEPTH,
) -> tuple[tuple[DeadRef, ...], tuple[Path, ...]]:
    """Breadth-first walk of the ``@``-import tree from ``root_path``.

    Returns dead imports (so the ``claude_md_dead_ref`` detector can
    surface them) alongside every live file reached. Visited files are
    deduped so cycles don't cause the walk to revisit work; depth is
    clamped at ``max_depth`` so pathological trees can't blow the
    stack.
    """
    dead: list[DeadRef] = []
    live: list[Path] = []
    visited: set[Path] = {root_path.resolve(strict=False)}
    queue: list[tuple[Path, str, int]] = [(root_path, root_text, 0)]
    while queue:
        parent_path, parent_text, parent_depth = queue.pop(0)
        if parent_depth >= max_depth:
            continue
        next_depth = parent_depth + 1
        parent_lines = parent_text.splitlines()
        parent_base = parent_path.parent if parent_path.name else parent_path
        for line_no, raw in _iter_import_candidates(parent_text):
            target = _resolve_import(raw, parent_base)
            if target is None:
                continue
            line_text = parent_lines[line_no - 1] if 0 < line_no <= len(parent_lines) else ""
            try:
                exists = target.exists()
            except OSError:
                exists = True
            if not exists:
                line_only_here = (
                    parent_path == root_path
                    and _line_is_only_ref(line_text, f"@{raw}")
                )
                dead.append(
                    DeadRef(
                        raw=f"@{raw}",
                        line_number=line_no,
                        line_text=line_text,
                        resolved=target,
                        line_only=line_only_here,
                        import_depth=next_depth,
                        import_parent=parent_path,
                    )
                )
                continue
            resolved_target = target.resolve(strict=False)
            if resolved_target in visited:
                continue
            visited.add(resolved_target)
            live.append(target)
            try:
                child_text = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            queue.append((target, child_text, next_depth))
    return tuple(dead), tuple(live)


def parse_claude_md(path: Path, text: str, counter: TokenCounter) -> ParsedClaudeMd:
    """Parse a CLAUDE.md file into measured sections + dead refs.

    ``path`` is the source file's absolute path — used as the base for
    resolving relative dead-ref candidates. ``text`` is the file
    contents (passed in so callers can re-use bytes already read
    elsewhere in the scan).
    """
    sections = parse_sections(text)
    measured = tuple(
        MeasuredSection(
            section=section,
            tokens=counter.count(section.body),
            body_hash=_body_hash(section),
        )
        for section in sections
    )
    base_dir = path.parent if path.name else path
    path_dead = _find_dead_refs(text, base_dir)
    import_dead, live_imports = _walk_import_chain(path, text)
    return ParsedClaudeMd(
        path=path,
        text=text,
        sections=measured,
        dead_refs=path_dead + import_dead,
        live_imports=live_imports,
    )
