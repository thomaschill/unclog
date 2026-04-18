"""Detect MCP servers declared in config but absent from the latest session.

Two signal sources, ranked by confidence:

1. ``--probe-mcps`` results, when the user opted in. A failed probe is
   ground truth: we tried to start the server and it couldn't come up.
   Stderr tail is attached so the user knows *why* it failed.

2. Session-inference fallback: if the user's last recorded session did
   NOT load an MCP server's tools, the server is either (a)
   misconfigured and failing to start, or (b) has never actually been
   invoked since config was written. We can't distinguish these without
   spawning the server, so the evidence note is explicit about the
   ambiguity and recommends ``--probe-mcps`` for certainty.

Auto-check is never set for ``dead_mcp`` (spec §6): the user may be
mid-fix, and disabling a real MCP they haven't yet wired up would be
counterproductive. Action is ``comment_out_mcp`` so the config entry
is preserved and reversible via snapshot.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from unclog.findings.base import Action, Finding, Scope
from unclog.findings.thresholds import Thresholds
from unclog.scan.stats import ActivityIndex
from unclog.state import InstallationState

_MCP_TOOL_PREFIX = "mcp__"


def _all_declared_servers(state: InstallationState) -> dict[str, Path | None]:
    """Collect every MCP server name across global + project configs.

    Returns name → first project path that declared it (or ``None`` for
    global). Name collisions between projects keep the first encountered,
    matching how the scan composes probe keys in :mod:`unclog.app`.
    """
    config = state.global_scope.config
    if config is None:
        return {}
    out: dict[str, Path | None] = {name: None for name in config.mcp_servers}
    for project in config.projects.values():
        for name in project.mcp_servers:
            out.setdefault(name, project.path)
    return out


def _scope_for(project_path: Path | None) -> Scope:
    if project_path is None:
        return Scope(kind="global")
    return Scope(kind="project", project_path=project_path)


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
    declared = _all_declared_servers(state)
    if not declared:
        return []

    probes = state.global_scope.mcp_probes
    findings: list[Finding] = []

    if probes:
        # High-confidence path: every failed probe becomes a dead_mcp.
        # Successful probes are not dead — whether or not the last
        # session happened to invoke them is irrelevant to dead_mcp.
        for name in sorted(declared):
            probe = probes.get(name)
            if probe is None or probe.ok:
                continue
            project_path = declared[name]
            findings.append(
                Finding(
                    id=f"dead_mcp:{name}",
                    type="dead_mcp",
                    title=f"MCP {name!r} failed to start",
                    reason=probe.error or "probe failed with no error message",
                    scope=_scope_for(project_path),
                    action=Action(primitive="comment_out_mcp", server_name=name),
                    auto_checked=False,
                    token_savings=None,
                    evidence={
                        "server_name": name,
                        "probe_error": probe.error,
                        "stderr_tail": probe.stderr_tail,
                        "duration_ms": probe.duration_ms,
                        "source": "probe",
                    },
                )
            )
        return findings

    # Fallback: session-inference. Lower confidence — flagged as such
    # in evidence so the user knows a probe would be more authoritative.
    session = state.global_scope.latest_session
    if session is None:
        return []

    loaded = _servers_loaded_in_session(state)
    for name in sorted(declared):
        if name in loaded:
            continue
        project_path = declared[name]
        findings.append(
            Finding(
                id=f"dead_mcp:{name}",
                type="dead_mcp",
                title=f"MCP {name!r} configured but not loaded in last session",
                reason="present in .claude.json, absent from latest session tools",
                scope=_scope_for(project_path),
                action=Action(primitive="comment_out_mcp", server_name=name),
                auto_checked=False,
                token_savings=None,
                evidence={
                    "server_name": name,
                    "session_path": str(session.session_path),
                    "source": "session_inference",
                    "note": (
                        "either failing to start or never invoked since being "
                        "added to config; run with --probe-mcps to distinguish"
                    ),
                },
            )
        )
    return findings
