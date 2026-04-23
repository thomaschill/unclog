"""Build the picker's inventory: every agent, skill, command, and MCP server.

Plugin-bundled agents/skills are excluded — they reinstall on the next
plugin update, so deleting individual files is the wrong action.
"""

from __future__ import annotations

from unclog.findings.base import Action, Finding, Scope
from unclog.scan.config import McpServer
from unclog.scan.tokens import TiktokenCounter
from unclog.state import InstallationState


def build_curate_findings(state: InstallationState) -> list[Finding]:
    """Return every removable item as a Finding, sorted by token cost desc."""
    counter = TiktokenCounter()
    findings: list[Finding] = []

    for agent in state.agents:
        descriptor = f"{agent.name}: {agent.description or ''}"
        tokens = counter.count(descriptor)
        findings.append(
            Finding(
                id=f"agent:{agent.slug}",
                type="agent_inventory",
                title=agent.name,
                scope=Scope(kind="global"),
                action=Action(primitive="delete_file", path=agent.path),
                token_savings=tokens if tokens > 0 else None,
            )
        )

    for skill in state.skills:
        descriptor = f"{skill.name}: {skill.description or ''}"
        tokens = counter.count(descriptor)
        findings.append(
            Finding(
                id=f"skill:{skill.slug}",
                type="skill_inventory",
                title=skill.name,
                scope=Scope(kind="global"),
                action=Action(primitive="delete_file", path=skill.directory),
                token_savings=tokens if tokens > 0 else None,
            )
        )

    for command in state.commands:
        descriptor = f"{command.name}: {command.description or ''}"
        tokens = counter.count(descriptor)
        findings.append(
            Finding(
                id=f"command:{command.slug}",
                type="command_inventory",
                title=command.name,
                scope=Scope(kind="global"),
                action=Action(primitive="delete_file", path=command.path),
                token_savings=tokens if tokens > 0 else None,
            )
        )

    findings.extend(_mcp_findings(state))

    findings.sort(key=lambda f: (-(f.token_savings or 0), f.title))
    return findings


def _mcp_findings(state: InstallationState) -> list[Finding]:
    """One finding per declared MCP server. Dedup by name, prefer global scope."""
    config = state.config
    if config is None:
        return []

    seen: dict[str, tuple[McpServer, Scope]] = {}
    for name, server in config.mcp_servers.items():
        seen[name] = (server, Scope(kind="global"))
    for project in config.projects.values():
        for name, server in project.mcp_servers.items():
            if name in seen:
                continue
            seen[name] = (server, Scope(kind="project", project_path=project.path))

    findings: list[Finding] = []
    for name in sorted(seen):
        _server, scope = seen[name]
        tokens = state.mcp_session_tokens.get(name)
        findings.append(
            Finding(
                id=f"mcp:{name}",
                type="mcp_inventory",
                title=name,
                scope=scope,
                action=Action(primitive="comment_out_mcp", server_name=name),
                token_savings=tokens if tokens else None,
            )
        )
    return findings


__all__ = ["build_curate_findings"]
