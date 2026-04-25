"""Finding, Action, and Scope — the records the picker and apply layer read."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

FindingType = Literal[
    "agent_inventory", "skill_inventory", "command_inventory", "mcp_inventory"
]
ScopeKind = Literal["global", "project"]
ActionPrimitive = Literal["delete_file", "remove_mcp"]


@dataclass(frozen=True)
class Scope:
    kind: ScopeKind
    project_path: Path | None = None


@dataclass(frozen=True)
class Action:
    primitive: ActionPrimitive
    path: Path | None = None
    server_name: str | None = None


@dataclass(frozen=True)
class Finding:
    """One item the user can remove from their Claude Code install.

    ``token_savings`` is the estimated cost of keeping the item loaded;
    ``None`` means we couldn't measure (e.g. an MCP whose schema wasn't
    in the last session's tools array — that's the common case).

    ``invocations`` is the count of ``tool_use`` blocks credited to this
    item across the recent session window. Set only for MCP findings;
    ``None`` for agents/skills/commands (no per-item invocation signal
    yet for those categories).
    """

    id: str
    type: FindingType
    title: str
    scope: Scope
    action: Action
    token_savings: int | None = None
    invocations: int | None = None
