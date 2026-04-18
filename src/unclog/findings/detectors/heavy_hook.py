"""Surface hooks that fire on every prompt.

Claude Code hooks inject their stdout into context on the events they
subscribe to. Two events fire on *every* prompt regardless of what the
user types — ``SessionStart`` (once per session) and
``UserPromptSubmit`` (every turn) — so hooks registered against them
are the cheapest to miss when auditing what's eating the baseline.

This detector is flag-only (spec §6): we don't know what a hook's
stdout looks like without running it, so we can't estimate token
savings. The finding exists to make the hook visible — the user
decides whether to keep, disable, or rewrite it.

``PreToolUse`` and ``PostToolUse`` are intentionally excluded: they
fire per tool invocation, which usually correlates with user activity,
so they're rarely "free bloat" the way every-turn hooks are.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from unclog.findings.base import Action, Finding, Scope
from unclog.findings.thresholds import Thresholds
from unclog.scan.config import Hook
from unclog.scan.stats import ActivityIndex
from unclog.state import InstallationState

_EVERY_TURN_EVENTS: frozenset[str] = frozenset({"SessionStart", "UserPromptSubmit"})


def detect(
    state: InstallationState,
    activity: ActivityIndex,
    thresholds: Thresholds,
    *,
    now: datetime,
) -> list[Finding]:
    findings: list[Finding] = []
    gs = state.global_scope
    if gs.settings is not None:
        for hook in gs.settings.hooks:
            finding = _maybe_flag(hook, project_path=None)
            if finding is not None:
                findings.append(finding)
    for project in state.project_scopes:
        for hook in project.hooks:
            finding = _maybe_flag(hook, project_path=project.path)
            if finding is not None:
                findings.append(finding)
    return findings


def _maybe_flag(hook: Hook, *, project_path: Path | None) -> Finding | None:
    if hook.event not in _EVERY_TURN_EVENTS:
        return None
    scope = (
        Scope(kind="project", project_path=project_path)
        if project_path is not None
        else Scope(kind="global")
    )
    where = project_path.name if project_path is not None else "global"
    title = f"{hook.event} hook fires every prompt ({where})"
    reason = (
        f"stdout from `{_abbreviate(hook.command)}` is injected into every "
        f"{'session start' if hook.event == 'SessionStart' else 'prompt'}"
    )
    return Finding(
        id=f"heavy_hook:{hook.source_scope}:{hook.event}:{_slug(hook.command)}",
        type="heavy_hook",
        title=title,
        reason=reason,
        scope=scope,
        action=Action(primitive="flag_only", path=hook.source_path),
        auto_checked=False,
        token_savings=None,
        evidence={
            "event": hook.event,
            "matcher": hook.matcher,
            "command": hook.command,
            "source_scope": hook.source_scope,
            "source_path": str(hook.source_path),
        },
    )


def _abbreviate(command: str) -> str:
    """Shorten a command for inline display without losing the head of the line."""
    trimmed = command.strip().splitlines()[0] if command.strip() else ""
    if len(trimmed) <= 60:
        return trimmed
    return trimmed[:57] + "..."


def _slug(command: str) -> str:
    """Stable, filesystem-safe slug derived from the hook command.

    Hook commands are arbitrary shell strings, but the finding ``id`` has
    to survive JSON round-trips without needing escapes — collapse
    whitespace and strip out punctuation that would confuse downstream
    consumers of the id.
    """
    trimmed = command.strip().splitlines()[0] if command.strip() else "empty"
    slug = "".join(ch if ch.isalnum() else "-" for ch in trimmed)
    slug = "-".join(part for part in slug.split("-") if part)
    return slug[:40] or "empty"
