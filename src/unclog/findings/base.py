"""Finding and action dataclasses shared by every detector.

A :class:`Finding` is the universal record each detector emits. The
shape is deliberately flat and JSON-serialisable so the same object
round-trips into the ``--json`` report schema (spec §10) without a
second mapping layer.

``Action`` enumerates the action primitives from spec §6.1. Every
finding carries exactly one. The apply phase (M5) will dispatch on
``Action.primitive`` — until then actions are descriptive only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

FindingType = Literal[
    "dead_mcp",
    "unused_mcp",
    "stale_plugin",
    "disabled_plugin_residue",
    "claude_md_dead_ref",
    "claude_md_duplicate",
    "claude_md_oversized",
    "scope_mismatch_global_to_project",
    "scope_mismatch_project_to_global",
    "missing_claudeignore",
    "heavy_hook",
]

ScopeKind = Literal["global", "project", "global_to_project", "project_to_global"]

ActionPrimitive = Literal[
    "delete_file",
    "comment_out_mcp",
    "disable_plugin",
    "uninstall_plugin",
    "remove_claude_md_section",
    "remove_claude_md_lines",
    "move_claude_md_section",
    "open_in_editor",
    "flag_only",
]


@dataclass(frozen=True)
class Scope:
    """Where a finding lives in the user's world."""

    kind: ScopeKind
    project_path: Path | None = None

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind}
        if self.project_path is not None:
            payload["project_path"] = str(self.project_path)
        return payload


@dataclass(frozen=True)
class Action:
    """A concrete, reversible operation that would resolve a finding.

    ``flag_only`` means the finding is informational — the apply layer
    will not offer to execute it.
    """

    primitive: ActionPrimitive
    path: Path | None = None
    server_name: str | None = None
    plugin_key: str | None = None
    heading: str | None = None
    line_numbers: tuple[int, ...] = ()

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"primitive": self.primitive}
        if self.path is not None:
            payload["path"] = str(self.path)
        if self.server_name is not None:
            payload["server_name"] = self.server_name
        if self.plugin_key is not None:
            payload["plugin_key"] = self.plugin_key
        if self.heading is not None:
            payload["heading"] = self.heading
        if self.line_numbers:
            payload["line_numbers"] = list(self.line_numbers)
        return payload


@dataclass(frozen=True)
class Finding:
    """A single cleanup opportunity surfaced to the user.

    ``id`` is the stable identifier used in snapshots and JSON output.
    Convention: ``<type>:<slug-or-key>`` so it is human-readable in logs
    (e.g. ``unused_mcp:notion``).

    ``token_savings`` is the detector's best estimate of the tokens that
    would be recovered by applying the action, or ``None`` if not
    measurable (residue flags, missing ``.claudeignore``).

    ``auto_checked`` mirrors spec §6's "Auto-check" column. Only
    conservative, high-confidence actions are pre-checked; anything that
    touches MCPs, plugins, or cross-scope moves requires explicit user
    selection.
    """

    id: str
    type: FindingType
    title: str
    reason: str
    scope: Scope
    action: Action
    auto_checked: bool
    token_savings: int | None = None
    evidence: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "reason": self.reason,
            "scope": self.scope.to_json(),
            "action": self.action.to_json(),
            "auto_checked": self.auto_checked,
            "token_savings": self.token_savings,
            "evidence": dict(self.evidence),
        }
