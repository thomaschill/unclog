"""Detect MCP servers declared in config but absent from the latest session.

v0.1 interpretation: if the user's last recorded session did NOT load
an MCP server's tools, the server is either (a) misconfigured and
failing to start, or (b) has never actually been invoked since config
was written. Both conditions are worth surfacing. We cannot distinguish
them without spawning the server, which v0.1 refuses to do (spec §5.4:
"Never spawn MCP servers to measure them in v0.1").

Auto-check is never set for ``dead_mcp`` (spec §6): the user may be
mid-fix, and disabling a real MCP they haven't yet wired up would be
counterproductive. Action is ``comment_out_mcp`` so the config entry
is preserved and reversible via snapshot.
"""

from __future__ import annotations

from datetime import datetime

from unclog.findings.base import Action, Finding, Scope
from unclog.findings.thresholds import Thresholds
from unclog.scan.stats import ActivityIndex
from unclog.state import InstallationState

_MCP_TOOL_PREFIX = "mcp__"


def _servers_loaded_in_session(state: InstallationState) -> set[str]:
    session = state.global_scope.latest_session
    if session is None:
        return set()
    servers: set[str] = set()
    for tool in session.tools:
        name = tool.get("name")
        if not isinstance(name, str) or not name.startswith(_MCP_TOOL_PREFIX):
            continue
        remainder = name[len(_MCP_TOOL_PREFIX) :]
        server, _, _ = remainder.partition("__")
        if server:
            servers.add(server)
    return servers


def detect(
    state: InstallationState,
    activity: ActivityIndex,
    thresholds: Thresholds,
    *,
    now: datetime,
) -> list[Finding]:
    config = state.global_scope.config
    if config is None or not config.mcp_servers:
        return []
    session = state.global_scope.latest_session
    if session is None:
        # No session yet; can't attribute. dead_mcp requires ground truth.
        return []

    loaded = _servers_loaded_in_session(state)
    findings: list[Finding] = []
    for name in sorted(config.mcp_servers):
        if name in loaded:
            continue
        findings.append(
            Finding(
                id=f"dead_mcp:{name}",
                type="dead_mcp",
                title=f"MCP {name!r} configured but not loaded in last session",
                reason="present in .claude.json, absent from latest session tools",
                scope=Scope(kind="global"),
                action=Action(primitive="comment_out_mcp", server_name=name),
                auto_checked=False,
                token_savings=None,
                evidence={
                    "server_name": name,
                    "session_path": str(session.session_path),
                    "note": (
                        "either failing to start or never invoked since being "
                        "added to config; unclog never spawns MCP servers to "
                        "probe them in v0.1"
                    ),
                },
            )
        )
    return findings
