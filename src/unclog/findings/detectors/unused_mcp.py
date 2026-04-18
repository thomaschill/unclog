"""Detect MCP servers that are loaded every session but never invoked.

Every MCP an installation ships with loads its tool schemas into every
session whether the user calls it or not. A server with, say, 40 tools
at ~200 tokens each is 8k tokens of schema overhead on every single
prompt — perpetual cost for zero value if nobody's calling it.

Two signal sources for "is this server loaded":

- **Probe (preferred)**: ``--probe-mcps`` confirmed the server actually
  starts and produces a tools schema. This works even when no session
  JSONL exists yet — fresh installs get coverage too.
- **Session fallback**: server appears in the latest session's ``tools``
  array. Lower confidence (schemas are sometimes absent from JSONL) but
  useful when probing is skipped.

Invocations are tallied across each project's latest session in
``scan_global`` and hang off :class:`GlobalScope.mcp_invocations`. Zero
invocations + confirmed-loaded → unused.

Auto-check is never set for ``unused_mcp`` (spec §6): removing an MCP a
user is mid-setup on would be counterproductive. Action is
``comment_out_mcp`` so the config is preserved and snapshot-reversible.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from unclog.findings.base import Action, Finding, Scope
from unclog.findings.thresholds import Thresholds
from unclog.scan.stats import ActivityIndex
from unclog.scan.tokens import TiktokenCounter
from unclog.state import InstallationState

_MCP_TOOL_PREFIX = "mcp__"


def _session_tokens(state: InstallationState) -> dict[str, int]:
    """Map each MCP server seen in the latest session to its schema tokens."""
    session = state.global_scope.latest_session
    if session is None:
        return {}
    counter = TiktokenCounter()
    per_server: dict[str, int] = {}
    for tool in session.tools:
        name = tool.get("name")
        if not isinstance(name, str) or not name.startswith(_MCP_TOOL_PREFIX):
            continue
        remainder = name[len(_MCP_TOOL_PREFIX) :]
        server, _, _ = remainder.partition("__")
        if not server:
            continue
        blob = json.dumps(tool, separators=(",", ":"))
        per_server[server] = per_server.get(server, 0) + counter.count(blob)
    return per_server


def _declaring_project(state: InstallationState, name: str) -> Path | None:
    """Return the project that declares ``name`` (or ``None`` for global)."""
    config = state.global_scope.config
    if config is None:
        return None
    if name in config.mcp_servers:
        return None
    for project in config.projects.values():
        if name in project.mcp_servers:
            return project.path
    return None


def detect(
    state: InstallationState,
    activity: ActivityIndex,
    thresholds: Thresholds,
    *,
    now: datetime,
) -> list[Finding]:
    del activity, thresholds, now  # signature parity with other detectors

    probes = state.global_scope.mcp_probes
    session_tokens = _session_tokens(state)
    invocations = state.global_scope.mcp_invocations
    session = state.global_scope.latest_session

    # Unified "loaded" signal: probe success beats session appearance. The
    # value is the per-server token cost to show in savings; the source
    # string lands on evidence for the JSON schema.
    loaded: dict[str, tuple[int, str]] = {}
    for name, probe in (probes or {}).items():
        if probe.ok and probe.tools_tokens:
            loaded[name] = (probe.tools_tokens, "probe")
    for name, tokens in session_tokens.items():
        if name not in loaded:
            loaded[name] = (tokens, "session")

    if not loaded:
        return []

    findings: list[Finding] = []
    for name in sorted(loaded):
        if invocations.get(name, 0) > 0:
            continue
        tokens, source = loaded[name]
        project_path = _declaring_project(state, name)
        scope = (
            Scope(kind="project", project_path=project_path)
            if project_path is not None
            else Scope(kind="global")
        )
        token_label = f"~{tokens:,} tok" if tokens else "unknown tokens"
        findings.append(
            Finding(
                id=f"unused_mcp:{name}",
                type="unused_mcp",
                title=f"MCP {name!r} loads {token_label} every session, 0 invocations in recent sessions",
                reason="tools schema loaded; zero tool_use records across recent sessions",
                scope=scope,
                action=Action(primitive="comment_out_mcp", server_name=name),
                auto_checked=False,
                token_savings=tokens or None,
                evidence={
                    "server_name": name,
                    "session_path": str(session.session_path) if session is not None else "",
                    "invocations_observed": 0,
                    "source": source,
                    "note": (
                        "invocation count aggregated across each project's latest "
                        "session; disable with comment_out_mcp, reversible via snapshot"
                    ),
                },
            )
        )
    return findings
