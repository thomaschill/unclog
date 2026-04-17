"""Render an :class:`InstallationState` to stdout in the requested format.

M2 swaps the byte-count estimates used in M1 for real tiktoken
measurements. Every composition entry now reports a concrete token
count. MCP servers are attributed from the ``tools`` array of the
most recent session JSONL across all known projects — we measure what
Claude Code actually injected rather than what might theoretically
load.

When no session JSONL is available anywhere (fresh installs, or a home
that has never opened a project), MCP entries stay marked
``unmeasured`` and the baseline degrades to the sum of file-backed
sources. Tokens for CLAUDE.md, skills, and agents are always measured
(we have the source bytes on disk).

``--json`` output keeps the stable ``unclog.v0.1`` schema shape. Field
names that used to advertise ``bytes_estimate`` now read ``tiktoken``
(or ``unmeasured`` for MCP without a session).
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console

from unclog import __version__
from unclog.findings import detect as detect_findings
from unclog.findings import load_thresholds
from unclog.findings.base import Finding
from unclog.scan.session import SessionSystemBlock
from unclog.scan.tokens import TiktokenCounter, TokenCounter
from unclog.state import InstallationState, tier_for_baseline
from unclog.ui.hero import render_hero, render_treemap
from unclog.ui.wordmark import wordmark
from unclog.util.paths import ClaudePaths

SCHEMA_ID = "unclog.v0.1"

# Token naming convention for MCP tools in Claude Code sessions:
# ``mcp__<server>__<tool>``. Built-in tools (Read, Write, Bash, etc.)
# do not carry this prefix.
_MCP_TOOL_PREFIX = "mcp__"


def _mcp_attribution(session: SessionSystemBlock, counter: TokenCounter) -> dict[str, int]:
    """Group session tools by MCP server name and sum per-tool token counts.

    Tools that don't match the ``mcp__<server>__`` convention (built-in
    Claude Code tools like ``Read`` / ``Bash``) are excluded.
    """
    per_server: dict[str, int] = {}
    for tool in session.tools:
        name = tool.get("name")
        if not isinstance(name, str) or not name.startswith(_MCP_TOOL_PREFIX):
            continue
        remainder = name[len(_MCP_TOOL_PREFIX) :]
        server_name, _, _ = remainder.partition("__")
        if not server_name:
            continue
        blob = json.dumps(tool, separators=(",", ":"))
        per_server[server_name] = per_server.get(server_name, 0) + counter.count(blob)
    return per_server


def build_composition(state: InstallationState, counter: TokenCounter) -> list[dict[str, Any]]:
    """Return the composition breakdown, largest first."""
    entries: list[dict[str, Any]] = []
    gs = state.global_scope

    if gs.claude_md_text:
        entries.append(
            {
                "source": "global:CLAUDE.md",
                "scope": "global",
                "bytes": gs.claude_md_bytes,
                "tokens": counter.count(gs.claude_md_text),
                "tokens_source": "tiktoken",
            }
        )
    if gs.claude_local_md_text:
        entries.append(
            {
                "source": "global:CLAUDE.local.md",
                "scope": "global",
                "bytes": gs.claude_local_md_bytes,
                "tokens": counter.count(gs.claude_local_md_text),
                "tokens_source": "tiktoken",
            }
        )

    if gs.skills:
        skill_tokens = sum(counter.count(f"{s.name}: {s.description or ''}") for s in gs.skills)
        skill_bytes = sum(s.frontmatter_bytes for s in gs.skills)
        entries.append(
            {
                "source": f"skills:descriptions (n={len(gs.skills)})",
                "scope": "global",
                "bytes": skill_bytes,
                "tokens": skill_tokens,
                "tokens_source": "tiktoken",
                "note": "name + description per skill, loaded on every session",
            }
        )

    if gs.agents:
        agent_tokens = sum(counter.count(f"{a.name}: {a.description or ''}") for a in gs.agents)
        agent_bytes = sum(a.frontmatter_bytes for a in gs.agents)
        entries.append(
            {
                "source": f"agents:descriptions (n={len(gs.agents)})",
                "scope": "global",
                "bytes": agent_bytes,
                "tokens": agent_tokens,
                "tokens_source": "tiktoken",
            }
        )

    mcp_servers = gs.config.mcp_servers if gs.config else {}
    session = gs.latest_session
    attribution = _mcp_attribution(session, counter) if session else {}
    for name in sorted(mcp_servers):
        measured = attribution.get(name)
        entries.append(
            {
                "source": f"mcp:{name}",
                "scope": "global",
                "bytes": None,
                "tokens": measured if measured is not None else None,
                "tokens_source": "session+tiktoken" if measured is not None else "unmeasured",
                "note": (
                    None
                    if measured is not None
                    else "no session JSONL found - open this MCP in Claude Code once to measure"
                ),
            }
        )

    def _rank(entry: dict[str, Any]) -> int:
        tokens = entry.get("tokens")
        return tokens if isinstance(tokens, int) else -1

    entries.sort(key=_rank, reverse=True)
    return entries


def _mcp_label(inv: dict[str, Any]) -> str:
    """Render the MCP-server count with project-scope breakdown when relevant.

    Project-scoped MCPs live in ``~/.claude.json``'s per-project sections
    and only load when that project is open. Surfacing them separately
    prevents the "0 MCP servers" confusion for users whose entire MCP
    footprint is project-scoped.
    """
    total = inv["mcp_servers"]
    project_scoped = inv.get("mcp_servers_project", 0)
    if total == 0:
        return "0 MCP servers"
    if project_scoped and project_scoped == total:
        return f"{total} MCP servers (project-scoped)"
    if project_scoped:
        return f"{total} MCP servers ({project_scoped} project-scoped)"
    return f"{total} MCP servers"


def _inventory(state: InstallationState) -> dict[str, int]:
    gs = state.global_scope
    global_mcp = len(gs.config.mcp_servers) if gs.config else 0
    project_mcp = 0
    if gs.config:
        for project in gs.config.projects.values():
            project_mcp += len(project.mcp_servers)
    return {
        "skills": len(gs.skills),
        "agents": len(gs.agents),
        "commands": len(gs.commands),
        "plugins": len(gs.installed_plugins),
        "mcp_servers": global_mcp + project_mcp,
        "mcp_servers_global": global_mcp,
        "mcp_servers_project": project_mcp,
        "projects_known": len(gs.config.projects) if gs.config else 0,
    }


def _baseline(
    composition: list[dict[str, Any]], session: SessionSystemBlock | None
) -> dict[str, Any]:
    attributed_tokens = sum(e["tokens"] for e in composition if isinstance(e.get("tokens"), int))
    unmeasured_sources = sum(1 for e in composition if e["tokens_source"] == "unmeasured")

    if session is not None:
        total = session.total_tokens
        tokens_source = "session+tiktoken"
    else:
        total = attributed_tokens
        tokens_source = "tiktoken" if attributed_tokens else "empty"

    return {
        "estimated_tokens": total,
        "attributed_tokens": attributed_tokens,
        "tokens_source": tokens_source,
        "tier": tier_for_baseline(total),
        "unmeasured_sources": unmeasured_sources,
        "session_path": str(session.session_path) if session else None,
    }


def _load_findings(state: InstallationState) -> list[Finding]:
    """Run all detectors against ``state`` using the user's thresholds.

    The threshold config is loaded relative to the scanned ``claude_home``
    so the same state can be reported from different CWDs without
    picking up ambient config.
    """
    paths = ClaudePaths(home=state.claude_home)
    thresholds = load_thresholds(paths.config_toml)
    return detect_findings(
        state,
        state.global_scope.activity,
        thresholds,
        now=state.generated_at,
    )


def build_report(state: InstallationState) -> dict[str, Any]:
    """Return the full machine-readable report as a plain dict."""
    counter = TiktokenCounter()
    composition = build_composition(state, counter)
    session = state.global_scope.latest_session
    findings = _load_findings(state)
    return {
        "schema": SCHEMA_ID,
        "unclog_version": __version__,
        "generated_at": state.generated_at.isoformat().replace("+00:00", "Z"),
        "claude_home": str(state.claude_home),
        "baseline": _baseline(composition, session),
        "inventory": _inventory(state),
        "composition": composition,
        "findings": [f.to_json() for f in findings],
        "warnings": list(state.warnings),
        "projects_audited": _projects_audited(state),
    }


def _projects_audited(state: InstallationState) -> list[dict[str, Any]]:
    """Summarise the project scopes the scan actually read.

    Used by the JSON schema and the rich/plain renderers so users see
    which CLAUDE.md files the cross-scope detectors had access to —
    the difference between "no findings because nothing's wrong" and
    "no findings because we didn't scan any projects".
    """
    return [
        {
            "path": str(project.path),
            "name": project.name,
            "exists": project.exists,
            "claude_md_bytes": project.claude_md_bytes,
            "claude_local_md_bytes": project.claude_local_md_bytes,
            "has_claudeignore": project.has_claudeignore,
        }
        for project in state.project_scopes
    ]


def baseline_tokens(state: InstallationState) -> int:
    """Compute the current baseline-token total for ``state``.

    Used by the interactive flow to seed the post-apply countdown (spec
    §11.8). Separate from :func:`build_report` to keep the countdown
    path light — no findings detection needed.
    """
    counter = TiktokenCounter()
    composition = build_composition(state, counter)
    session = state.global_scope.latest_session
    return int(_baseline(composition, session)["estimated_tokens"] or 0)


def render_json(state: InstallationState) -> str:
    """Render the state as a single JSON document."""
    return json.dumps(build_report(state), indent=2, sort_keys=False)


def render_plain(state: InstallationState) -> str:
    """ASCII-only, colour-free text render. Used for ``--plain`` and non-TTY."""
    report = build_report(state)
    lines: list[str] = []
    lines.append(f"unclog {report['unclog_version']}  |  schema {report['schema']}")
    lines.append(f"claude_home: {report['claude_home']}")
    lines.append("")
    baseline = report["baseline"]
    lines.append(
        f"baseline: ~{baseline['estimated_tokens']:,} tokens  "
        f"[{baseline['tier']}]  ({baseline['tokens_source']})"
    )
    if baseline.get("unmeasured_sources"):
        lines.append(
            f"  ({baseline['unmeasured_sources']} MCP source(s) unmeasured - "
            f"open them in Claude Code to record a session)"
        )
    lines.append("")
    inv = report["inventory"]
    lines.append(
        "inventory: "
        f"{inv['skills']} skills | {inv['agents']} agents | "
        f"{inv['commands']} commands | {inv['plugins']} plugins | "
        f"{_mcp_label(inv)} | "
        f"{inv['projects_known']} known projects"
    )
    if report["projects_audited"]:
        lines.append("")
        lines.append("projects audited:")
        for project in report["projects_audited"]:
            missing = " (missing)" if not project["exists"] else ""
            ci = " .claudeignore" if project["has_claudeignore"] else ""
            lines.append(f"  - {project['name']}{missing}{ci}  {project['path']}")
    if report["composition"]:
        lines.append("")
        lines.append("composition (largest first):")
        for entry in report["composition"]:
            tokens = entry.get("tokens")
            size = "unmeasured" if tokens is None else f"{tokens:>8,} tok"
            lines.append(f"  {size}  {entry['source']}")
    if report["findings"]:
        lines.append("")
        auto = sum(1 for f in report["findings"] if f.get("auto_checked"))
        info = sum(
            1
            for f in report["findings"]
            if f.get("action", {}).get("primitive") == "flag_only"
        )
        opt_in = len(report["findings"]) - auto - info
        parts = [f"{auto} auto-fix"]
        if opt_in:
            parts.append(f"{opt_in} opt-in")
        if info:
            parts.append(f"{info} informational")
        lines.append(
            f"findings: {len(report['findings'])} ({', '.join(parts)})"
        )
        for f in report["findings"]:
            primitive = f.get("action", {}).get("primitive")
            if f.get("auto_checked"):
                marker = "[x]"
            elif primitive == "flag_only":
                marker = "[i]"
            else:
                marker = "[ ]"
            scope = f["scope"].get("kind", "global")
            lines.append(f"  {marker} [{scope:>7}] {f['title']} - {f['reason']}")
    else:
        lines.append("")
        lines.append("findings: none")
    if report["warnings"]:
        lines.append("")
        lines.append("warnings:")
        for w in report["warnings"]:
            lines.append(f"  ! {w}")
    return "\n".join(lines) + "\n"


def render_rich(
    state: InstallationState,
    console: Console,
    *,
    show_wordmark: bool = True,
) -> None:
    """Pretty TTY render: wordmark, hero, treemap, inventory.

    Spec §11.4 suppresses the wordmark in ``--report``/``--json``/``--plain``.
    The CLI resolves :class:`~unclog.ui.display.DisplayOptions` and passes
    ``show_wordmark`` so this renderer stays ignorant of mode flags.
    """
    report = build_report(state)
    baseline = report["baseline"]
    inv = report["inventory"]

    if show_wordmark:
        console.print(wordmark())
        console.print("")
    console.print(render_hero(baseline))
    console.print("")
    if report["composition"]:
        console.print(render_treemap(report["composition"]))
        console.print("")
    console.print(
        "[dim]"
        f"{inv['skills']} skills · {inv['agents']} agents · "
        f"{inv['commands']} commands · {inv['plugins']} plugins · "
        f"{_mcp_label(inv)} · "
        f"{inv['projects_known']} known projects"
        "[/dim]"
    )
    _render_findings_rich(report["findings"], console)
    if report["warnings"]:
        console.print("")
        for warning in report["warnings"]:
            console.print(f"[#eab308]![/#eab308] [dim]{warning}[/dim]")


def _render_findings_rich(findings: list[dict[str, Any]], console: Console) -> None:
    """Render the findings list block in the TTY renderer."""
    console.print("")
    if not findings:
        console.print("[dim]findings: none — nothing to clean up right now[/dim]")
        return
    auto = sum(1 for f in findings if f.get("auto_checked"))
    info = sum(1 for f in findings if f.get("action", {}).get("primitive") == "flag_only")
    opt_in = len(findings) - auto - info
    parts = [f"{auto} auto-fix"]
    if opt_in:
        parts.append(f"{opt_in} opt-in")
    if info:
        parts.append(f"{info} informational")
    console.print(
        f"[bold]Found {len(findings)} issue(s).[/bold] "
        f"[dim]{', '.join(parts)}.[/dim]"
    )
    for f in findings:
        primitive = f.get("action", {}).get("primitive")
        if f.get("auto_checked"):
            marker = "[#22c55e][x][/#22c55e]"
        elif primitive == "flag_only":
            marker = "[dim][i][/dim]"
        else:
            marker = "[dim][ ][/dim]"
        scope_kind = f["scope"].get("kind", "global")
        scope_label = f"[dim][{scope_kind:>7}][/dim]"
        console.print(f" {marker} {scope_label} {f['title']}  [dim]· {f['reason']}[/dim]")
