"""Enumerate every local agent and skill as an individually-actionable Finding.

The detectors in :mod:`unclog.findings.detectors` only flag items unclog
can *prove* are suspect (dead MCPs, residue, scope mismatches). Agents
and skills don't get detector flags at all — we can't reliably infer
"used" from session JSONL (Claude dispatches agents via the ``Task``
tool with ``subagent_type``, which we don't fingerprint), so any
detector would be guessing.

Instead, this module builds a parallel *curate* list: every local agent,
every local skill, and every remote (SSE/HTTP) MCP server — items where
we know they exist but can't prove they should go. Users opt in to
reviewing them via the secondary picker.

Remote MCPs land here rather than in detector findings because we
can't probe them locally (no ``command`` for stdio). Their tools load
on every turn whether you call them or not, so even without a token
count the user is well-served by getting a one-click option to comment
one out.

Plugin-bundled content is intentionally excluded — plugins already
appear as one row each in ``stale_plugin`` findings, and deleting files
inside a plugin install path gets undone on the next plugin update.
The correct action for plugin content is ``disable_plugin`` on the
whole plugin, which already exists in the primary picker.
"""

from __future__ import annotations

from unclog.findings.base import Action, Finding, Scope
from unclog.scan.config import McpServer
from unclog.scan.tokens import TiktokenCounter
from unclog.state import InstallationState

_REMOTE_TRANSPORTS = frozenset({"sse", "http"})


def _transport(server: McpServer) -> str | None:
    value = server.raw.get("type")
    return value if isinstance(value, str) else None


def build_curate_findings(state: InstallationState) -> list[Finding]:
    """Return every local agent and skill as a Finding, sorted by token cost desc.

    Each Finding has ``auto_checked=False`` — curate is always opt-in
    per-item selection; we never pre-check anything in this list.
    """
    counter = TiktokenCounter()
    findings: list[Finding] = []
    gs = state.global_scope

    for agent in gs.agents:
        descriptor = f"{agent.name}: {agent.description or ''}"
        tokens = counter.count(descriptor)
        findings.append(
            Finding(
                id=f"agent_inventory:{agent.slug}",
                type="agent_inventory",
                title=f"Agent {agent.name!r}",
                reason=(agent.description or "no description").strip()[:160],
                scope=Scope(kind="global"),
                action=Action(primitive="delete_file", path=agent.path),
                auto_checked=False,
                token_savings=tokens if tokens > 0 else None,
                evidence={
                    "slug": agent.slug,
                    "path": str(agent.path),
                    "description": agent.description or "",
                    "note": (
                        "name + description are loaded into every session; "
                        "body content only loads when the agent is invoked"
                    ),
                },
            )
        )

    for skill in gs.skills:
        descriptor = f"{skill.name}: {skill.description or ''}"
        tokens = counter.count(descriptor)
        findings.append(
            Finding(
                id=f"skill_inventory:{skill.slug}",
                type="skill_inventory",
                title=f"Skill {skill.name!r}",
                reason=(skill.description or "no description").strip()[:160],
                scope=Scope(kind="global"),
                action=Action(primitive="delete_file", path=skill.directory),
                auto_checked=False,
                token_savings=tokens if tokens > 0 else None,
                evidence={
                    "slug": skill.slug,
                    "directory": str(skill.directory),
                    "description": skill.description or "",
                    "note": (
                        "name + description are loaded into every session; "
                        "full SKILL.md body only loads when the skill is invoked"
                    ),
                },
            )
        )

    findings.extend(_remote_mcp_findings(state))

    findings.sort(key=lambda f: (-(f.token_savings or 0), f.title))
    return findings


def _remote_mcp_findings(state: InstallationState) -> list[Finding]:
    """One curate-row per remote (SSE/HTTP) MCP declared in the user's config.

    Deduped by server name: the same remote MCP declared in N projects
    gets a single finding, scoped to the first project that owns it.
    We can't measure token cost (no local handshake), so
    ``token_savings`` stays ``None`` and the picker shows ``— tok`` —
    the row is worth keeping anyway so users can kill stale remotes.
    """
    config = state.global_scope.config
    if config is None:
        return []

    seen: dict[str, tuple[McpServer, Scope]] = {}
    for name, server in config.mcp_servers.items():
        if _transport(server) in _REMOTE_TRANSPORTS:
            seen[name] = (server, Scope(kind="global"))
    for project in config.projects.values():
        for name, server in project.mcp_servers.items():
            if name in seen:
                continue
            if _transport(server) in _REMOTE_TRANSPORTS:
                seen[name] = (server, Scope(kind="project", project_path=project.path))

    findings: list[Finding] = []
    for name in sorted(seen):
        server, scope = seen[name]
        transport = _transport(server) or "remote"
        url = server.raw.get("url")
        findings.append(
            Finding(
                id=f"unmeasured_mcp:{name}",
                type="unmeasured_mcp",
                title=f"MCP {name!r} ({transport.upper()})",
                reason=(
                    "remote MCP — tools schema loads every turn but we can't "
                    "probe its cost without opening a live connection"
                ),
                scope=scope,
                action=Action(primitive="comment_out_mcp", server_name=name),
                auto_checked=False,
                token_savings=None,
                evidence={
                    "server_name": name,
                    "transport": transport,
                    "url": url if isinstance(url, str) else None,
                    "note": (
                        "disable with comment_out_mcp; snapshot-reversible if "
                        "you decide later you actually want this server"
                    ),
                },
            )
        )
    return findings


__all__ = ["build_curate_findings"]
