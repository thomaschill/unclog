"""Aggregate activity signals from ``stats-cache.json`` and ``history.jsonl``.

v0.1 scope:

- ``stats-cache.json`` contributes install-wide last-active date and
  first-session date. It tracks aggregate daily messages / model usage,
  not per-skill / per-agent / per-command invocation counts, so it can
  only tell us whether Claude Code itself is active.
- ``history.jsonl`` is the prompt stream. Each record has a ``display``
  (the user's typed input), a millisecond ``timestamp``, and a
  ``project`` path. We scan it for two signals detectors need:

  1. per-project last-active timestamp (stale-project detection)
  2. slash-command usage: the first token of ``display`` when it starts
     with ``/`` and looks like ``/<slug>``.

Per-skill and per-agent usage is NOT in v0.1 — it requires parsing full
session JSONL for tool_use records (spec §6, deferred to v0.2). We
record ``@slug`` mentions opportunistically because some users reference
skills by name, but detectors treat the absence of a mention as
*weak* evidence of non-use, never a proof.

All readers are permissive: unknown fields ignored, malformed lines
skipped, missing files return an empty index.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from types import MappingProxyType

# Matches "/word-name" at the start of a prompt. Slash commands are
# kebab-case, so we accept [a-z0-9-] up to whitespace or end.
_SLASH_COMMAND_RE = re.compile(r"^/([a-zA-Z0-9][a-zA-Z0-9_-]*)")
# Matches "@word-name" anywhere. Skill / agent mentions aren't a formal
# syntax in Claude Code, but users commonly reference them this way.
_AT_MENTION_RE = re.compile(r"@([a-zA-Z0-9][a-zA-Z0-9_-]*)")


@dataclass(frozen=True)
class ActivityIndex:
    """Aggregated activity signals computed once per scan.

    All timestamps are timezone-aware UTC. ``None`` means "no evidence of
    any activity" — distinct from "last activity was long ago", which
    detectors handle by comparing against a threshold.
    """

    last_active_overall: datetime | None = None
    first_session_at: datetime | None = None
    total_sessions: int = 0
    total_messages: int = 0
    per_project_last_active: Mapping[str, datetime] = field(
        default_factory=lambda: MappingProxyType({})
    )
    slash_command_last_used: Mapping[str, datetime] = field(
        default_factory=lambda: MappingProxyType({})
    )
    at_mention_last_used: Mapping[str, datetime] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def age_days(self, reference: datetime, *, of: datetime | None) -> int | None:
        """Return days between ``reference`` and ``of``, or ``None`` if unknown."""
        if of is None:
            return None
        delta = reference - of
        return max(delta.days, 0)


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    # stats-cache.json uses ``"2026-04-15"`` (date only) for dailyActivity
    # entries and full ISO strings for firstSessionDate / longestSession.
    try:
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            d = date.fromisoformat(text)
            return datetime(d.year, d.month, d.day, tzinfo=UTC)
        # Accept both "Z" and explicit offsets.
        normalised = text.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalised)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    except ValueError:
        return None


def _load_stats_cache(path: Path) -> tuple[datetime | None, datetime | None, int, int]:
    """Return ``(last_active, first_session, total_sessions, total_messages)``."""
    if not path.is_file():
        return None, None, 0, 0
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None, 0, 0
    if not isinstance(raw, dict):
        return None, None, 0, 0

    first_session = _parse_iso_datetime(raw.get("firstSessionDate"))

    last_active: datetime | None = None
    daily = raw.get("dailyActivity")
    if isinstance(daily, list):
        for entry in daily:
            if not isinstance(entry, dict):
                continue
            when = _parse_iso_datetime(entry.get("date"))
            if when is None:
                continue
            if last_active is None or when > last_active:
                last_active = when

    last_computed = _parse_iso_datetime(raw.get("lastComputedDate"))
    if last_computed is not None and (last_active is None or last_computed > last_active):
        last_active = last_computed

    total_sessions_raw = raw.get("totalSessions")
    total_sessions = total_sessions_raw if isinstance(total_sessions_raw, int) else 0
    total_messages_raw = raw.get("totalMessages")
    total_messages = total_messages_raw if isinstance(total_messages_raw, int) else 0

    return last_active, first_session, total_sessions, total_messages


def _scan_history(path: Path) -> tuple[
    Mapping[str, datetime],
    Mapping[str, datetime],
    Mapping[str, datetime],
    datetime | None,
]:
    """Return ``(per_project, slash_commands, at_mentions, last_overall)``."""
    if not path.is_file():
        return MappingProxyType({}), MappingProxyType({}), MappingProxyType({}), None

    per_project: dict[str, datetime] = {}
    slash_commands: dict[str, datetime] = {}
    at_mentions: dict[str, datetime] = {}
    last_overall: datetime | None = None

    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue

                ts_ms = record.get("timestamp")
                if not isinstance(ts_ms, (int, float)):
                    continue
                try:
                    when = datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC)
                except (OSError, OverflowError, ValueError):
                    continue

                if last_overall is None or when > last_overall:
                    last_overall = when

                project = record.get("project")
                if isinstance(project, str) and project:
                    previous = per_project.get(project)
                    if previous is None or when > previous:
                        per_project[project] = when

                display = record.get("display")
                if isinstance(display, str) and display:
                    stripped = display.lstrip()
                    slash_match = _SLASH_COMMAND_RE.match(stripped)
                    if slash_match:
                        slug = slash_match.group(1)
                        previous = slash_commands.get(slug)
                        if previous is None or when > previous:
                            slash_commands[slug] = when
                    for mention in _AT_MENTION_RE.finditer(stripped):
                        slug = mention.group(1)
                        previous = at_mentions.get(slug)
                        if previous is None or when > previous:
                            at_mentions[slug] = when
    except OSError:
        pass

    return (
        MappingProxyType(per_project),
        MappingProxyType(slash_commands),
        MappingProxyType(at_mentions),
        last_overall,
    )


def load_activity_index(stats_cache_path: Path, history_path: Path) -> ActivityIndex:
    """Build an :class:`ActivityIndex` from the two install-wide files.

    Both paths are allowed to be missing. Returns an index whose every
    field is safely empty in that case.
    """
    stats_last_active, first_session, total_sessions, total_messages = _load_stats_cache(
        stats_cache_path
    )
    per_project, slash_commands, at_mentions, history_last_overall = _scan_history(history_path)

    # history.jsonl has per-second precision and is the authoritative
    # "when did the user actually type something" source. stats-cache's
    # dailyActivity only resolves to the day. Prefer history when we
    # have it; fall back to stats-cache otherwise.
    candidates = [t for t in (history_last_overall, stats_last_active) if t is not None]
    last_active_overall = max(candidates) if candidates else None

    return ActivityIndex(
        last_active_overall=last_active_overall,
        first_session_at=first_session,
        total_sessions=total_sessions,
        total_messages=total_messages,
        per_project_last_active=per_project,
        slash_command_last_used=slash_commands,
        at_mention_last_used=at_mentions,
    )
